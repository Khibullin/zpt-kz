"""Practical document, item and data workspace using the established ledger."""
import hashlib
import uuid
from collections import defaultdict
from decimal import Decimal
from io import BytesIO
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum, Prefetch, Case, When, Value, BooleanField
from django.forms import formset_factory
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from .permissions import accounting_access
from .models import (GoodsDocument, DocumentAttachment, StockDocument, StockEntry,
    StockEvent, Item, ImportBatch, StockSnapshot, KaspiOperation, SaleReconciliation)
from .bundles import describe, document_identity, attach, post_bundle, receive_bundle
from .tools_views import xlsx_response

WAREHOUSES = [('', 'Все склады'), ('PP1', 'PP1 · Asia Grade'), ('PP2', 'PP2 · Rapida')]


class JournalFilter(forms.Form):
    q = forms.CharField(label='Накладная, поставщик, товар, артикул, заказ', required=False)
    warehouse = forms.ChoiceField(label='Склад', choices=WAREHOUSES, required=False)
    start = forms.DateField(label='С даты', required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    end = forms.DateField(label='По дату', required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    state = forms.ChoiceField(label='Состояние', required=False, choices=[('', 'Все'), ('draft', 'Черновики'), ('posted', 'Проведённые / принятые'), ('transit', 'В пути / частичная приёмка'), ('cancelled', 'Отменённые')])
    def clean(self):
        data = super().clean()
        if data.get('start') and data.get('end') and data['start'] > data['end']:
            self.add_error('end', 'Конечная дата раньше начальной.')
        return data


def filtered_bundles(request, kind=None):
    rows = GoodsDocument.objects.select_related('purchase_order__supplier').prefetch_related('lines__item', Prefetch('attachments',queryset=DocumentAttachment.objects.defer('data'))).order_by('-date', '-pk')
    if kind:
        rows = rows.filter(kind=kind)
    form = JournalFilter(request.GET)
    if not form.is_valid():
        return rows.none(), form
    f = form.cleaned_data
    if f['q']:
        q = f['q']; search = Q(number__icontains=q) | Q(counterparty__icontains=q) | Q(lines__item__name__icontains=q) | Q(lines__item__article__icontains=q) | Q(lines__item__code__icontains=q)
        num = q.upper().replace('ЗК-', '')
        if num.isdigit():
            search |= Q(purchase_order_id=int(num))
        rows = rows.filter(search).distinct()
    if f['warehouse']:
        rows = rows.filter(Q(warehouse=f['warehouse']) | Q(destination=f['warehouse']))
    if f['start']: rows = rows.filter(date__gte=f['start'])
    if f['end']: rows = rows.filter(date__lte=f['end'])
    return rows, form


@never_cache
@accounting_access
def journal(request, kind=None):
    rows, form = filtered_bundles(request, kind)
    result = [describe(b) for b in rows]
    state = form.cleaned_data.get('state') if form.is_valid() else ''
    if state == 'draft': result = [b for b in result if b.can_post]
    if state == 'posted': result = [b for b in result if b.state in ['Проведён', 'Принято полностью']]
    if state == 'transit': result = [b for b in result if b.state in ['В пути', 'Принято частично']]
    if state == 'cancelled': result = [b for b in result if b.state == 'Отменён']
    total = sum((b.amount for b in result if b.amount is not None), Decimal(0))
    if request.GET.get('export') == 'xlsx':
        return xlsx_response('goods-documents', ['Документ', 'Дата', 'Приёмка', 'Операция', 'Поставщик', 'Склад', 'Получатель', 'Позиций', 'Сумма, ₸', 'Статус'],
            ([b.number, b.date, b.accepted_on, b.get_kind_display(), b.counterparty, b.warehouse, b.destination, b.position_count, b.amount, b.state] for b in result))
    query = request.GET.copy(); query.pop('page', None)
    return render(request, 'inventory/journal.html', {'title': {'receipt': 'Приходы и накладные', 'transfer': 'Перемещения', 'sale': 'Продажи и возвраты'}.get(kind, 'Все складские документы'), 'kind': kind or '', 'form': form,
        'page': Paginator(result, 40).get_page(request.GET.get('page')), 'query': query.urlencode(), 'count': len(result), 'total': total,
        'unknown': sum(b.amount is None for b in result), 'acceptance_count': sum(b.acceptance_count for b in result), 'draft_count': sum(b.can_post for b in result)})


class BundleForm(forms.Form):
    key = forms.UUIDField(widget=forms.HiddenInput)
    version = forms.IntegerField(widget=forms.HiddenInput,required=False)
    number = forms.CharField(label='Номер накладной / документа', max_length=120, required=False)
    date = forms.DateField(label='Дата документа', initial=timezone.localdate, widget=forms.DateInput(attrs={'type': 'date'}))
    accepted_on = forms.DateField(label='Дата фактической приёмки / отправки', initial=timezone.localdate, widget=forms.DateInput(attrs={'type': 'date'}))
    warehouse = forms.ChoiceField(label='Склад', choices=WAREHOUSES[1:], initial='PP2')
    destination = forms.ChoiceField(label='Склад получения', choices=WAREHOUSES[1:], required=False)
    counterparty = forms.CharField(label='Поставщик / основание', max_length=250, required=False)
    note = forms.CharField(label='Комментарий', widget=forms.Textarea, required=False)
    file = forms.FileField(label='Оригинал накладной / акт / фото', required=False)
    def __init__(self, *args, kind='receipt', **kwargs):
        self.kind = kind
        super().__init__(*args, **kwargs)
        if kind != 'transfer': self.fields.pop('destination')
        if kind == 'receipt': self.fields['counterparty'].required = True; self.fields['number'].required = True
        if kind == 'sale': self.fields['number'].label = 'Номер заказа Kaspi'; self.fields['number'].required = True
    def clean(self):
        d = super().clean()
        if d.get('accepted_on') and d['accepted_on'] > timezone.localdate(): self.add_error('accepted_on', 'Фактическая операция не может быть в будущем.')
        if self.kind == 'transfer' and d.get('warehouse') == d.get('destination'): self.add_error('destination', 'Выберите другой склад.')
        if self.kind in ['writeoff', 'adjustment'] and not d.get('note'): self.add_error('note', 'Укажите причину корректировки.')
        return d


class BundleLine(forms.Form):
    item = forms.ModelChoiceField(label='Товар из каталога', queryset=Item.objects.order_by('article', 'brand'), required=False)
    article = forms.CharField(label='Артикул нового товара', max_length=120, required=False)
    brand = forms.CharField(label='Бренд', max_length=120, required=False)
    name = forms.CharField(label='Название нового товара', max_length=300, required=False)
    unit = forms.ChoiceField(label='Единица учёта', choices=[('', 'Выберите'), ('piece', 'Штука'), ('set', 'Комплект')], required=False)
    packages = forms.IntegerField(label='Количество по документу', min_value=1, max_value=10000000)
    multiplier = forms.IntegerField(label='Единиц учёта в упаковке', min_value=1, max_value=100000, initial=1)
    cost = forms.DecimalField(label='Цена за упаковку / единицу документа, ₸', max_digits=14, decimal_places=2, min_value=0, required=False)
    related_sale = forms.ModelChoiceField(label='Исходная отгрузка', queryset=StockDocument.objects.filter(kind='sale', status='posted'), required=False)
    return_condition = forms.ChoiceField(label='Состояние возврата', choices=[('good', 'Годен к продаже'), ('damaged', 'Брак')], required=False)
    sale_amount = forms.DecimalField(label='Сумма продажи / возврата строки, ₸', max_digits=14, decimal_places=2, min_value=0, required=False)
    selling_costs = forms.DecimalField(label='Расходы продажи / возвращённые расходы строки, ₸', max_digits=14, decimal_places=2, min_value=0, required=False)
    def __init__(self, *args, kind='receipt', **kwargs):
        self.kind = kind
        super().__init__(*args, **kwargs)
        if kind != 'receipt':
            for key in ['article', 'brand', 'name', 'unit']: self.fields.pop(key)
            self.fields['item'].required = True
        if kind not in ['sale', 'return']:
            self.fields.pop('sale_amount'); self.fields.pop('selling_costs')
        if kind != 'return': self.fields.pop('related_sale'); self.fields.pop('return_condition')
        if kind not in ['receipt', 'opening', 'adjustment']: self.fields.pop('cost')
        if kind == 'receipt': self.fields['cost'].required = True
        if kind == 'return': self.fields['related_sale'].required = True; self.fields['return_condition'].required = True
    def clean(self):
        d = super().clean()
        if self.kind == 'receipt' and not d.get('item') and not all(d.get(k) for k in ['article', 'brand', 'name', 'unit']):
            raise forms.ValidationError('Выберите товар либо заполните артикул, бренд, название и единицу нового товара.')
        if d.get('item') and d['item'].unit == 'pending': self.add_error('item', 'Сначала подтвердите единицу товара в его карточке.')
        if d.get('packages') and d.get('multiplier') and d['packages'] * d['multiplier'] > 100000000:
            self.add_error('packages', 'Слишком большое количество.')
        if d.get('cost') is not None and d.get('multiplier') and d['cost'] / d['multiplier'] != (d['cost'] / d['multiplier']).quantize(Decimal('.01')):
            self.add_error('cost', 'Цена в пересчёте на единицу должна быть точной до тиына. Укажите количество и цену в единицах учёта.')
        return d


@never_cache
@accounting_access
def new_document(request, kind, pk=None):
    if kind not in ['receipt', 'transfer', 'sale', 'return', 'writeoff', 'opening', 'adjustment']: raise Http404
    editing=get_object_or_404(GoodsDocument,pk=pk,kind=kind,evidence_only=False) if pk else None
    initial={'key':uuid.uuid4()};initial_lines=[]
    if editing:
        active=list(editing.lines.exclude(status='cancelled').order_by('pk'))
        if not active or any(d.status!='draft' for d in active):
            messages.error(request,'Изменить можно только непроведённый черновик.');return redirect('inventory:goods_detail',pk=pk)
        initial={k:getattr(editing,k) for k in ['number','date','accepted_on','warehouse','destination','counterparty','note']}
        initial.update(key=editing.request_key,version=max(d.pk for d in active))
        initial_lines=[{'item':d.item_id,'packages':d.quantity,'multiplier':1,'cost':d.unit_cost,'related_sale':d.related_sale_id,'return_condition':d.return_condition,'sale_amount':d.sale_amount,'selling_costs':d.selling_costs} for d in active]
    form = BundleForm(request.POST or None, request.FILES or None, kind=kind, initial=initial)
    Lines = formset_factory(BundleLine, extra=0 if editing else 1, can_delete=True, max_num=300, absolute_max=300, validate_max=True)
    lines = Lines(request.POST or None, prefix='lines', form_kwargs={'kind': kind},initial=initial_lines)
    if request.method == 'POST' and form.is_valid() and lines.is_valid():
        try:
            with transaction.atomic():
                d = form.cleaned_data
                existing = GoodsDocument.objects.filter(request_key=d['key']).first() if not editing else None
                if existing: return redirect('inventory:goods_detail', pk=existing.pk)
                identity = document_identity(kind, d['number'], d['date'], d['counterparty'], d['warehouse'], d.get('destination', ''))
                if identity and GoodsDocument.objects.filter(identity=identity).exclude(pk=pk).exists():
                    raise ValidationError('Документ с такими реквизитами уже существует. Найдите его в журнале; повторный приход не создан.')
                if editing:
                    bundle=GoodsDocument.objects.select_for_update().get(pk=pk)
                    old=list(bundle.lines.select_for_update().exclude(status='cancelled'))
                    if d['key']!=bundle.request_key or not old or any(line.status!='draft' for line in old) or max(line.pk for line in old)!=d['version']:
                        raise ValidationError('Черновик уже изменён или проведён. Откройте его заново.')
                    for old_line in old:
                        old_line.status='cancelled';old_line.save(update_fields=['status'])
                        StockEvent.objects.create(document=old_line,action='Заменена строка черновика',quantity=old_line.quantity,user=request.user)
                    for key in ['number','date','accepted_on','warehouse','counterparty','note']:setattr(bundle,key,d[key])
                    bundle.destination=d.get('destination','');bundle.identity=identity;bundle.save()
                else:
                    bundle = GoodsDocument.objects.create(kind=kind, number=d['number'], date=d['date'], accepted_on=d['accepted_on'], warehouse=d['warehouse'], destination=d.get('destination', ''), counterparty=d['counterparty'], note=d['note'], request_key=d['key'], identity=identity, created_by=request.user)
                usable = [f.cleaned_data for f in lines if f.cleaned_data and not f.cleaned_data.get('DELETE')]
                if not usable: raise ValidationError('Добавьте хотя бы один товар.')
                seen = set()
                for f in usable:
                    item = f.get('item')
                    if not item:
                        # Exact identity only. Existing ambiguous cards are never guessed.
                        matches = Item.objects.filter(article=f['article'], brand=f['brand'])
                        if matches.count() > 1: raise ValidationError('Найдено несколько товаров этого бренда и артикула. Выберите карточку явно.')
                        item = matches.first()
                        if item and item.unit != f['unit']: raise ValidationError('Единица отличается от существующего товара.')
                        if not item: item = Item.objects.create(code='ART-' + uuid.uuid4().hex[:20], article=f['article'], brand=f['brand'], name=f['name'], unit=f['unit'])
                    if item.pk in seen: raise ValidationError('Один товар указан дважды. Объедините количество в одну строку.')
                    seen.add(item.pk)
                    note = f"По документу: {f['packages']} × {f['multiplier']} единиц учёта. " + d['note']
                    StockDocument.objects.create(bundle=bundle, kind=kind, item=item, quantity=f['packages'] * f['multiplier'], unit_cost=f['cost'] / f['multiplier'] if f.get('cost') is not None else None,
                        warehouse=d['warehouse'], destination=d.get('destination', ''), counterparty=d['counterparty'], external_number=d['number'], date=d['accepted_on'], note=note,
                        related_sale=f.get('related_sale'), return_condition=f.get('return_condition') or '', sale_amount=f.get('sale_amount'), selling_costs=f.get('selling_costs'), created_by=request.user,
                        receipt_key=d['key'] if kind == 'receipt' else None)
                if d.get('file'): attach(bundle, d['file'], request.user)
            messages.success(request, 'Черновик сохранён. Проверьте состав и проведите документ отдельной кнопкой.')
            return redirect('inventory:goods_detail', pk=bundle.pk)
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, '; '.join(exc.messages) if isinstance(exc, ValidationError) else 'Документ или товар уже создан другим действием. Проверьте журнал.')
    return render(request, 'inventory/goods_form.html', {'form': form, 'lines': lines, 'kind': kind, 'title': dict(StockDocument.Kind.choices)[kind]})


class AttachmentForm(forms.Form):
    file = forms.FileField(label='Прикрепить оригинал или акт (до 15 МБ)')


@never_cache
@accounting_access
def goods_detail(request, pk):
    bundle = get_object_or_404(GoodsDocument.objects.select_related('purchase_order'), pk=pk)
    form = AttachmentForm()
    if request.method == 'POST':
        try:
            action = request.POST.get('action')
            if action == 'post': post_bundle(pk, request.user); messages.success(request, 'Документ проведён. Все строки отражены в учёте.')
            elif action == 'receive':
                receive_bundle(pk, {str(d.pk): request.POST.get('received_' + str(d.pk), '0') for d in bundle.lines.all()}, request.user)
                messages.success(request, 'Приёмка сохранена; непринятый товар остаётся в пути.')
            elif action == 'attach':
                form = AttachmentForm(request.POST, request.FILES)
                if form.is_valid(): attach(bundle, form.cleaned_data['file'], request.user); messages.success(request, 'Оригинал прикреплён. Количества не изменены.')
                else: return render_detail(request, bundle, form)
            elif action == 'cancel':
                with transaction.atomic():
                    locked = GoodsDocument.objects.select_for_update().get(pk=pk)
                    docs = list(locked.lines.select_for_update().exclude(status='cancelled'))
                    if any(d.status != 'draft' for d in docs): raise ValidationError('Отменить можно только целый черновик. Проведённые операции сохраняются.')
                    for doc in docs:
                        doc.status = 'cancelled'; doc.save(update_fields=['status'])
                        StockEvent.objects.create(document=doc, action='Отменён черновик', quantity=doc.quantity, user=request.user)
                messages.success(request, 'Черновик отменён. Документ сохранён в истории.')
            else: raise ValidationError('Неизвестное действие.')
        except (ValidationError, IntegrityError) as exc:
            messages.error(request, '; '.join(exc.messages) if isinstance(exc, ValidationError) else 'Операция уже существует. Повторное проведение не выполнено.')
        return redirect('inventory:goods_detail', pk=pk)
    return render_detail(request, bundle, form)


def render_detail(request, bundle, form):
    docs = list(bundle.lines.select_related('item', 'related_sale').prefetch_related('events__user').order_by('pk'))
    for d in docs:
        d.remaining = d.quantity - d.received_quantity
        d.amount = d.quantity * d.unit_cost if d.unit_cost is not None else None
        d.open_lots = []
    return render(request, 'inventory/goods_detail.html', {'document': describe(bundle), 'lines': docs, 'attachment_form': form, 'attachments': bundle.attachments.defer('data')})


@never_cache
@accounting_access
def attachment(request, pk):
    obj = get_object_or_404(DocumentAttachment, pk=pk)
    response = FileResponse(BytesIO(bytes(obj.data)), as_attachment=True, filename=obj.name)
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@never_cache
@accounting_access
def item_detail(request, pk):
    from .stock_reporting import stock_rows
    from .costing import available_lots
    item = get_object_or_404(Item.objects.prefetch_related('aliases', 'supplier_links__supplier'), pk=pk)
    row = stock_rows([item])[0]
    movements = StockEntry.objects.filter(document__item=item).select_related('document__bundle', 'created_by').order_by('-pk')
    lots = []; lot_error = ''
    try:
        for warehouse, entries in available_lots(item, include_transit=True).items():
            for lot in entries:
                origin = StockDocument.objects.select_related('bundle').get(pk=lot['origin'])
                lots.append({**lot, 'warehouse': warehouse, 'document': origin})
    except ValidationError as exc:
        lot_error = '; '.join(exc.messages)
    sources = StockDocument.objects.filter(item=item).select_related('bundle', 'purchase_order').order_by('-date', '-pk')
    if request.GET.get('export') == 'xlsx': return xlsx_response('item-movements', ['Дата', 'Документ', 'Операция', 'Склад', 'Изменение'], ([m.effective_on, m.document.external_number, m.document.get_kind_display(), m.warehouse, m.quantity] for m in movements))
    return render(request, 'inventory/item_detail.html', {'item': row, 'movements': movements[:300], 'movement_count': movements.count(), 'lots': lots, 'lot_error': lot_error, 'sources': sources[:300]})


@never_cache
@accounting_access
def hub(request, section):
    if section not in ['reports', 'data', 'money', 'sales']: raise Http404
    return render(request, 'inventory/hub.html', {'section': section, 'batches': ImportBatch.objects.defer('original_data').annotate(has_original=Case(When(original_data__isnull=False,then=Value(True)),default=Value(False),output_field=BooleanField())).order_by('-pk')[:100], 'unlinked': KaspiOperation.objects.filter(reconciliation__isnull=True).count(),
        'document_count': GoodsDocument.objects.count(), 'sale_count': StockDocument.objects.filter(kind='sale', status='posted').count()})


@never_cache
@accounting_access
def batch_file(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk)
    if not batch.original_data: raise Http404('Оригинал старой загрузки не сохранён. Загрузите тот же файл повторно, чтобы прикрепить оригинал без повторных операций.')
    return FileResponse(BytesIO(bytes(batch.original_data)), as_attachment=True, filename=batch.filename)


class SaleLinkForm(forms.Form):
    document = forms.ModelChoiceField(label='Проведённая отгрузка / возврат', queryset=StockDocument.objects.none())
    checked = forms.BooleanField(label='Проверены номер заказа, товар, количество и тип операции. Эта связь не создаёт повторного списания.')
    def __init__(self, *args, operation, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['document'].queryset = StockDocument.objects.filter(kind__in=['sale', 'return'], status='posted', external_number=operation.order_id).select_related('item')


@never_cache
@accounting_access
def sale_link(request, pk):
    operation = get_object_or_404(KaspiOperation, pk=pk)
    form = SaleLinkForm(request.POST or None, operation=operation)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            locked = KaspiOperation.objects.select_for_update().get(pk=pk)
            existing, created = SaleReconciliation.objects.get_or_create(operation=locked, defaults={'document': form.cleaned_data['document'], 'confirmed_by': request.user})
            if not created and existing.document_id != form.cleaned_data['document'].pk: form.add_error(None, 'Строка уже связана с другой отгрузкой.')
            else:
                messages.success(request, 'Связь сохранена. Складской остаток не изменён; финансовые суммы не перенесены повторно.')
                return redirect('inventory:sales')
    return render(request, 'inventory/form.html', {'form': form, 'title': f'Сопоставить Kaspi {operation.order_id} · {operation.product}'})


@never_cache
@accounting_access
def overview(request):
    from .stock_reporting import stock_rows
    from procurement.models import PurchaseOrder
    from .purchasing import order_summary
    items = stock_rows()
    receipts = list(GoodsDocument.objects.filter(kind='receipt').prefetch_related('lines__item').order_by('-pk')[:10])
    orders = [o for o in PurchaseOrder.objects.filter(user=request.user, archived=False).select_related('supplier')]
    for o in orders: o.workflow = order_summary(o)
    warehouses = []
    for code, label in WAREHOUSES[1:]:
        field = code.lower(); groups = defaultdict(int)
        for item in items:
            if getattr(item, field) is not None: groups[item.get_unit_display()] += getattr(item, field)
        warehouses.append({'code': code, 'label': label, 'groups': dict(groups), 'ledger_count': sum(getattr(i, field + '_posted', False) for i in items), 'snapshot_count': sum(not getattr(i, field + '_posted', False) and getattr(i, field) is not None for i in items)})
    return render(request, 'inventory/overview.html', {'warehouses': warehouses, 'item_count': len(items), 'receipts': [describe(b) for b in receipts], 'receipt_count': GoodsDocument.objects.filter(kind='receipt').count(), 'draft_count': StockDocument.objects.filter(status='draft').count(),
        'transit_count': GoodsDocument.objects.filter(kind='transfer', lines__status__in=['sent', 'partial']).distinct().count(), 'pending_units': Item.objects.filter(unit='pending').count(),
        'unlinked_sales': KaspiOperation.objects.filter(reconciliation__isnull=True).count(), 'orders': orders[:8], 'batches': ImportBatch.objects.order_by('-pk')[:5]})


class BalanceDateForm(forms.Form):
    date = forms.DateField(label='Остатки на конец дня', initial=timezone.localdate, widget=forms.DateInput(attrs={'type':'date'}))
    warehouse = forms.ChoiceField(label='Склад', choices=WAREHOUSES, required=False)
    q = forms.CharField(label='Товар, артикул', required=False)


@never_cache
@accounting_access
def balances_at(request):
    form = BalanceDateForm(request.GET if request.GET else {'date':timezone.localdate().isoformat()})
    rows = []
    if form.is_valid():
        f = form.cleaned_data
        entries = StockEntry.objects.filter(effective_on__lte=f['date'])
        if f['warehouse']: entries = entries.filter(warehouse=f['warehouse'])
        if f['q']: entries = entries.filter(Q(document__item__article__icontains=f['q'])|Q(document__item__name__icontains=f['q']))
        rows = list(entries.values('document__item_id','document__item__article','document__item__brand','document__item__name','document__item__unit','warehouse').annotate(quantity=Sum('quantity')).order_by('document__item__article','warehouse'))
        labels = dict(Item._meta.get_field('unit').choices)
        for row in rows: row['unit'] = labels.get(row['document__item__unit'])
        if request.GET.get('export') == 'xlsx':
            return xlsx_response('balances-at', ['Артикул','Бренд','Товар','Склад','Количество','Единица','На дату'], ([r['document__item__article'],r['document__item__brand'],r['document__item__name'],r['warehouse'],r['quantity'],r['unit'],f['date']] for r in rows))
    return render(request,'inventory/balances_at.html',{'form':form,'rows':rows})


class RestoreOrderForm(forms.Form):
    from procurement.models import Supplier
    number = forms.IntegerField(label='Номер отсутствующего заказа ЗК', min_value=1, max_value=999999)
    supplier = forms.ModelChoiceField(label='Поставщик',queryset=Supplier.objects.all())
    date = forms.DateField(label='Дата исходного заказа',widget=forms.DateInput(attrs={'type':'date'}))
    warehouse = forms.ChoiceField(label='Склад поставки',choices=WAREHOUSES[1:],initial='PP2')
    total = forms.DecimalField(label='Контрольная сумма заказа, ₸',max_digits=16,decimal_places=2,min_value=0)
    composition = forms.CharField(label='Проверенный состав (JSON: article, brand, name, quantity, cost)',widget=forms.Textarea)
    source = forms.FileField(label='Исходный документ PDF / XLSX / фото')
    reason = forms.CharField(label='Основание восстановления',widget=forms.Textarea)
    def clean_composition(self):
        import json
        raw = self.cleaned_data['composition']
        if len(raw)>200000: raise forms.ValidationError('Слишком большой состав.')
        try:
            rows=json.loads(raw)
            if not isinstance(rows,list) or not 1<=len(rows)<=300: raise ValueError()
            seen=set()
            for r in rows:
                if not all(isinstance(r.get(k),str) and r[k].strip() for k in ['article','brand','name']): raise ValueError()
                if not isinstance(r.get('quantity'),int) or isinstance(r['quantity'],bool) or not 1<=r['quantity']<=1000000: raise ValueError()
                cost=Decimal(str(r.get('cost')))
                if not cost.is_finite() or cost<0 or cost!=cost.quantize(Decimal('.01')): raise ValueError()
                r['cost']=str(cost)
                key=(r['article'],r['brand'])
                if key in seen: raise ValueError()
                seen.add(key)
            return rows
        except Exception:
            raise forms.ValidationError('Проверьте JSON, точные бренд/артикул, целое положительное количество и цену.')


@never_cache
@accounting_access
def restore_order(request):
    from procurement.models import PurchaseOrder, PurchaseDocument, Activity
    from procurement.permissions import workspace_role
    if workspace_role(request.user) != 'admin':
        return render(request,'procurement/access_denied.html',status=403)
    form=RestoreOrderForm(request.POST or None,request.FILES or None)
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic():
                d=form.cleaned_data
                # The supplier lock also serializes concurrent restores of its missing order.
                type(d['supplier']).objects.select_for_update().get(pk=d['supplier'].pk)
                if PurchaseOrder.objects.filter(pk=d['number']).exists(): raise ValidationError('Номер заказа уже занят. Существующий заказ не изменён.')
                total=sum((Decimal(r['cost'])*r['quantity'] for r in d['composition']),Decimal(0))
                if total!=d['total']: raise ValidationError('Контрольная сумма не совпадает с составом.')
                if d['date']>timezone.localdate(): raise ValidationError('Дата исходного заказа не может быть в будущем.')
                file=d['source']
                if file.size>15*1024*1024: raise ValidationError('Оригинал не более 15 МБ.')
                if file.name.rsplit('.',1)[-1].lower() not in ['pdf','xlsx','jpg','jpeg','png']: raise ValidationError('Прикрепите исходный PDF, XLSX или фото.')
                order=PurchaseOrder.objects.create(pk=d['number'],user=request.user,supplier=d['supplier'],request_key=uuid.uuid4(),items=d['composition'],original_items=d['composition'],estimated_total=total,destination=d['warehouse'],status='confirmed',delivery_stage='waiting',comment='Восстановлено по исходному документу. '+d['reason'])
                PurchaseOrder.objects.filter(pk=order.pk).update(created_at=timezone.make_aware(__import__('datetime').datetime.combine(d['date'],__import__('datetime').time(12))))
                PurchaseDocument.objects.create(order=order,kind='waybill',name=file.name[:255],data=file.read())
                # Explicit restored PK must not leave the PostgreSQL sequence behind.
                from django.db import connection
                from django.core.management.color import no_style
                for sql in connection.ops.sequence_reset_sql(no_style(),[PurchaseOrder]):
                    with connection.cursor() as cursor: cursor.execute(sql)
                Activity.objects.create(user=request.user,action='purchase_restored',details={'purchase':order.pk,'reason':d['reason']})
            messages.success(request,'Заказ восстановлен. Фактическая приёмка оформляется отдельно; остатки пока не изменены.')
            return redirect('procurement:purchase_detail',pk=order.pk)
        except (ValidationError,IntegrityError) as exc:
            form.add_error(None,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Номер уже занят; повторное восстановление не выполнено.')
    return render(request,'inventory/form.html',{'form':form,'title':'Восстановить отсутствующий заказ по оригиналу','multipart':True})


@never_cache
@accounting_access
def search(request):
    from procurement.models import PurchaseOrder
    q=request.GET.get('q','').strip()[:250]
    items=Item.objects.none();documents=GoodsDocument.objects.none();orders=PurchaseOrder.objects.none()
    if q:
        items=Item.objects.filter(Q(article__icontains=q)|Q(name__icontains=q)|Q(code__icontains=q)|Q(aliases__code__icontains=q)).distinct()[:100]
        documents=GoodsDocument.objects.filter(Q(number__icontains=q)|Q(counterparty__icontains=q)|Q(note__icontains=q)|Q(source_lines__icontains=q)).order_by('-date')[:100]
        orders=PurchaseOrder.objects.filter(user=request.user).filter(Q(supplier__name__icontains=q)|Q(title__icontains=q)|Q(waybill_number__icontains=q))
        number=q.upper().replace('ЗК-','')
        if number.isdigit():orders=PurchaseOrder.objects.filter(user=request.user).filter(Q(pk=int(number))|Q(supplier_order_number__icontains=q))
        orders=orders[:100]
    return render(request,'inventory/search.html',{'q':q,'items':items,'documents':documents,'orders':orders})


class ArchiveTransferForm(forms.Form):
    number=forms.CharField(label='Название / номер передачи',max_length=120)
    date=forms.DateField(label='Дата документа передачи',widget=forms.DateInput(attrs={'type':'date'}))
    plan=forms.FileField(label='Накладная перемещения XLSX')
    act=forms.FileField(label='Акт фактической приёмки Rapida XLSX',required=False)
    calculation=forms.FileField(label='Расчёт перемещения XLSX',required=False)
    balance=forms.FileField(label='Остатки после перемещения XLSX',required=False)
    note=forms.CharField(label='Основание и пояснение дат',widget=forms.Textarea)


@never_cache
@accounting_access
def archive_transfer(request):
    form=ArchiveTransferForm(request.POST or None,request.FILES or None)
    if request.method=='POST' and form.is_valid():
        try:
            from openpyxl import load_workbook
            from .importing import parse_file
            from zipfile import ZipFile
            d=form.cleaned_data;file=d['plan']
            if file.size>10*1024*1024:raise ValidationError('Накладная — не более 10 МБ.')
            raw=file.read();file.seek(0)
            with ZipFile(BytesIO(raw)) as z:
                if sum(i.file_size for i in z.infolist())>50*1024*1024:raise ValidationError('Распакованный файл слишком большой.')
            book=load_workbook(BytesIO(raw),data_only=True,read_only=True)
            try:
                from itertools import islice
                rows=list(islice(book.worksheets[0].values,20001))
            finally:book.close()
            if len(rows)>20000:raise ValidationError('Не более 20 000 строк.')
            header=next((i for i,r in enumerate(rows) if 'Артикул' in r and 'Количество' in r),None)
            if header is None:raise ValidationError('Нужна накладная с колонками «Артикул», «Ед. изм.», «Количество».')
            columns={v:i for i,v in enumerate(rows[header]) if v};lines=[];seen=set()
            for row in rows[header+1:]:
                if not row or not isinstance(row[0],int):continue
                article=str(row[columns['Артикул']] or '').strip();quantity=row[columns['Количество']]
                if not article or not isinstance(quantity,(int,float)) or quantity<1 or int(quantity)!=quantity:raise ValidationError('Некорректная строка накладной.')
                if article in seen:raise ValidationError('Повтор артикула в накладной.')
                seen.add(article)
                lines.append({'article':article,'unit':str(row[columns['Ед. изм.']] or 'Не указана'),'planned':int(quantity),'accepted':None})
            if not lines:raise ValidationError('В накладной нет товаров.')
            act_rows=[]
            if d.get('act'):
                act_rows=parse_file(d['act'].read(),'rapida');d['act'].seek(0)
                by_article={r['article']:r for r in act_rows}
                for line in lines:
                    fact=by_article.get(line['article'])
                    if fact:line.update(accepted=fact['accepted'],barcode=fact['barcode'],act_planned=fact['planned'])
            identity=document_identity('transfer',d['number'],d['date'],'PP1 → Rapida','PP1','PP2')
            with transaction.atomic():
                doc,created=GoodsDocument.objects.get_or_create(identity=identity,defaults={'kind':'transfer','number':d['number'],'date':d['date'],'warehouse':'PP1','destination':'PP2','counterparty':'PP1 → Rapida','note':d['note'],'evidence_only':True,'source_lines':lines,'created_by':request.user})
                if not doc.evidence_only or doc.source_lines!=lines:raise ValidationError('Документ с такими реквизитами уже есть с другим составом. Проверьте архив.')
                for key in ['plan','act','calculation','balance']:
                    if d.get(key):attach(doc,d[key],request.user)
            messages.success(request,'Историческое перемещение и оригиналы сохранены. Текущие остатки не пересчитаны повторно.')
            return redirect('inventory:goods_detail',pk=doc.pk)
        except (ValidationError,ValueError,KeyError,IndexError,IntegrityError) as exc:
            form.add_error(None,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Проверьте формат исходных файлов и уникальность документа.')
    return render(request,'inventory/form.html',{'form':form,'title':'Добавить историческое перемещение и акт Rapida'})


@never_cache
@accounting_access
def edit_goods(request,pk):
    bundle=get_object_or_404(GoodsDocument,pk=pk)
    return new_document(request,bundle.kind,pk=pk)
