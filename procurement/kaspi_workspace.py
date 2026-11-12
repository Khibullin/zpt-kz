"""Purchased batches and local card drafts. No Kaspi requests or stock writes."""
import hashlib
import json
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.core import signing
from django.db import transaction
from django.db.models import Case, DecimalField, Exists, F, OuterRef, Prefetch, Q, Subquery, Sum, When
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from inventory.models import CashEntry, GoodsDocument, StockDocument, StockEntry, SupplierItem
from inventory.permissions import can_access_accounting
from .forms import DraftForm
from .models import Activity, Draft, Offer, PurchaseOrder, VehicleFitment
from .views import access, context


def batch_url(key, **params):
    return reverse('procurement:kaspi_products') + '?' + urlencode({'batch': key, **params})


def purchased_batches(user):
    """Keep existing ownership; standalone accounting receipts need accounting access."""
    receipt = StockDocument.objects.filter(purchase_order_id=OuterRef('pk'), kind='receipt', status='posted')
    payment = CashEntry.objects.filter(purchase_order_id=OuterRef('pk'), cancelled=False,
        kind__in=['supplier', 'supplier_refund']).order_by().values('purchase_order_id').annotate(
            net=Sum(Case(When(kind='supplier', then=F('amount')), default=-F('amount'),
                output_field=DecimalField(max_digits=26, decimal_places=2)))).values('net')
    orders = PurchaseOrder.objects.filter(user=user, archived=False).exclude(
        Q(status='cancelled') | Q(delivery_stage='cancelled')).annotate(has_receipt=Exists(receipt),
            paid_amount=Subquery(payment)).filter(
        Q(status__in=['paid', 'received']) | Q(has_receipt=True) | Q(paid_amount__gt=0)).select_related('supplier')
    batches = []
    for order in orders:
        batches.append(dict(key=f'order:{order.pk}', kind='order', obj=order,
            title=order.display_title, supplier=order.supplier.name,
            reference=order.order_number, date=order.created_at.date(), total=order.total,
            source_url=reverse('procurement:purchase_detail', args=[order.pk])))
    if can_access_accounting(user):
        posted = StockDocument.objects.filter(bundle_id=OuterRef('pk'), kind='receipt', status='posted')
        receipts = GoodsDocument.objects.filter(kind='receipt', evidence_only=False,
            purchase_order__isnull=True).annotate(has_posted=Exists(posted)).filter(has_posted=True)
        for document in receipts:
            batches.append(dict(key=f'receipt:{document.pk}', kind='receipt', obj=document,
                title=f'{document.counterparty} · закупка № {document.pk}', supplier=document.counterparty,
                reference=document.number, date=document.date, total=None,
                source_url=reverse('inventory:goods_detail', args=[document.pk])))
    return sorted(batches, key=lambda b: (b['date'], b['key']), reverse=True)


def source_rows(batch, user):
    obj = batch['obj']
    if batch['kind'] == 'order':
        from inventory.purchase_receiving import received_quantities
        received = received_quantities(obj)
        rows = [dict(index=i, source_id=i, **{k: item.get(k, '') for k in
            ('brand', 'article', 'name', 'cost', 'quantity')}, received=received.get(i, 0),
            unit='По заказу', source_note='') for i, item in enumerate(obj.items) if item.get('quantity', 0) > 0]
        documents = list(obj.documents.defer('data').all())
        files = [dict(name=d.name, date=d.created_at,
            url=reverse('procurement:purchase_document', args=[obj.pk, d.pk])) for d in documents]
        # Original receipt attachments belong to the same batch, not another purchase.
        if can_access_accounting(user):
            for receipt in obj.goods_documents.all():
                files.extend(dict(name=a.name, date=a.created_at,
                    url=reverse('inventory:attachment', args=[a.pk])) for a in receipt.attachments.defer('data'))
        supplier_id = obj.supplier_id
    else:
        lines = obj.lines.filter(kind='receipt', status='posted').select_related('item').order_by('pk')
        rows = [dict(index=i, source_id=line.pk, brand=line.item.brand, article=line.item.article,
            name=line.item.name, cost=line.unit_cost, quantity=line.quantity, received=line.quantity,
            item_id=line.item_id, unit=line.item.get_unit_display(), source_note=line.note)
            for i, line in enumerate(lines)]
        files = [dict(name=a.name, date=a.created_at, url=reverse('inventory:attachment', args=[a.pk]))
            for a in obj.attachments.defer('data')]
        # SupplierItem is an explicit link. Do not guess another supplier by name.
        supplier_ids = set(SupplierItem.objects.filter(item_id__in=[r['item_id'] for r in rows]).values_list('supplier_id', flat=True))
        supplier_id = next(iter(supplier_ids)) if len(supplier_ids) == 1 else None
        # The original Omega document uses the Russian legal name.
        if supplier_id is None and obj.counterparty.strip().casefold() in ('омега', 'omega'):
            from .models import Supplier
            supplier_id = Supplier.objects.filter(name__iexact='Omega').values_list('pk', flat=True).first()
    batch['positions'] = len(rows)
    batch['quantity'] = sum(r['quantity'] for r in rows)
    costs_known = all(r['cost'] is not None and r['cost'] != '' for r in rows)
    batch['total'] = sum((Decimal(str(r['cost'])) * r['quantity'] for r in rows), Decimal(0)) if costs_known else None
    return rows, files, supplier_id


def enrich_rows(batch, rows, supplier_id):
    identities = Q(pk__in=[])
    for row in rows:
        if row['brand'] and row['article']:
            identities |= Q(brand__iexact=row['brand'], article__iexact=row['article'])
    offers = Offer.objects.filter(identities, supplier_id=supplier_id).select_related('draft').prefetch_related(
        Prefetch('fitments', queryset=VehicleFitment.objects.filter(verified=True))) if supplier_id else []
    matches = {}
    for offer in offers:
        matches.setdefault((offer.brand.casefold(), offer.article.casefold()), []).append(offer)
    # Stock is shown separately from purchased quantity, never inferred from it.
    item_ids = [r['item_id'] for r in rows if r.get('item_id')]
    stock = {r['document__item_id']: r['quantity'] for r in StockEntry.objects.filter(
        document__item_id__in=item_ids, warehouse=batch['obj'].warehouse).values('document__item_id').annotate(
            quantity=Sum('quantity'))} if batch['kind'] == 'receipt' else {}
    for row in rows:
        candidates = matches.get((row['brand'].casefold(), row['article'].casefold()), [])
        offer = candidates[0] if len(candidates) == 1 else None
        draft = getattr(offer, 'draft', None) if offer else None
        row.update(offer=offer, draft=draft, fitments=list(offer.fitments.all()) if offer else [],
            photo=(draft.photo_url if draft else offer.photo_url if offer else ''),
            description=(draft.description if draft else offer.description if offer else ''),
            card_title=(draft.title if draft else row['name']),
            stock=stock.get(row.get('item_id')), link_issue=not bool(offer),
            url=batch_url(batch['key'], line=row['index']))
        row['checks'] = [('Фото', bool(row['photo'])), ('Описание', bool(row['description'])),
            ('Применимость', bool(row['fitments']))]
        row['missing'] = [label for label, ok in row['checks'] if not ok]
        row['status'] = 'Сверить товар' if not offer else 'Нужны данные' if row['missing'] else 'Черновик сохранён' if draft else 'Материалы собраны'
        row['status_code'] = 'missing' if row['missing'] or not offer else 'collected'
    return rows


def source_signature(batch, rows):
    data = [(r['source_id'], r['brand'], r['article'], r['quantity'], str(r['cost'])) for r in rows]
    return hashlib.sha256(json.dumps([batch['key'], data], ensure_ascii=False).encode()).hexdigest()


def draft_values(offer):
    # Provenance and internal review metadata are not product specifications.
    characteristics = {key: value for key, value in offer.characteristics.items()
        if not key.startswith(('omega_', 'shared_', 'reviewed_', 'manufacturer_', 'supplier_catalogue_', 'kaspi_'))}
    return {field: getattr(offer, field) for field in ('description', 'photo_url', 'category')} | {
        'title': offer.name, 'characteristics': characteristics}


@never_cache
@access()
@require_http_methods(['GET', 'POST'])
def kaspi_products(request):
    batches = purchased_batches(request.user)
    requested = request.GET.get('batch')
    batch = next((b for b in batches if b['key'] == requested), None) if requested else (batches[0] if batches else None)
    if requested and batch is None:
        raise Http404('Закупка недоступна.')
    rows, files = [], []
    active = None
    form = None
    events = []
    if batch:
        rows, files, supplier_id = source_rows(batch, request.user)
        enrich_rows(batch, rows, supplier_id)
        token = signing.dumps({'batch': batch['key'], 'source': source_signature(batch, rows)}, salt='kaspi-workspace')
        try:
            index = int(request.GET.get('line', rows[0]['index'] if rows else -1))
        except ValueError:
            raise Http404
        active = next((r for r in rows if r['index'] == index), None)
        if 'line' in request.GET and active is None:
            raise Http404
        if active and active['offer'] and request.procurement_role in ('sales', 'admin'):
            form = DraftForm(request.POST if request.method == 'POST' and request.POST.get('action') == 'save' else None,
                instance=active['draft'], initial=draft_values(active['offer']) if not active['draft'] else None)
        if request.method == 'POST':
            if request.procurement_role not in ('sales', 'admin'):
                return HttpResponse('Сохранение доступно менеджеру или администратору.', status=403)
            try:
                original = signing.loads(request.POST.get('source_token', ''), salt='kaspi-workspace', max_age=86400)
            except signing.BadSignature:
                return HttpResponse('Обновите страницу закупки перед сохранением.', status=409)
            if original != {'batch': batch['key'], 'source': source_signature(batch, rows)}:
                return HttpResponse('Состав закупки изменился. Обновите страницу.', status=409)
            action = request.POST.get('action')
            if action == 'prepare':
                selected = request.POST.getlist('selected')
                if not selected or any(not s.isdigit() for s in selected):
                    messages.error(request, 'Выберите товары для подготовки.')
                    return redirect(batch_url(batch['key']))
                chosen = [r for r in rows if str(r['index']) in selected]
                if len(chosen) != len(set(selected)):
                    return HttpResponse('Позиция отсутствует в закупке.', status=400)
                created = 0
                with transaction.atomic():
                    for row in chosen:
                        if not row['offer']:
                            continue
                        Offer.objects.select_for_update().get(pk=row['offer'].pk)
                        obj, new = Draft.objects.get_or_create(offer=row['offer'], defaults={**draft_values(row['offer']), 'updated_by': request.user})
                        if new:
                            created += 1
                            Activity.objects.create(user=request.user, action='kaspi_batch_prepared', details={
                                'kaspi_batch': batch['key'], 'line': row['index'], 'offer': row['offer'].pk,
                                'brand': row['brand'], 'article': row['article'],
                                'after': {k: getattr(obj, k) for k in ('title', 'description', 'photo_url', 'category', 'characteristics')}})
                messages.success(request, f'Новых черновиков: {created}. Существующие сохранены. Позиции без точной связи с каталогом требуют сверки. В Kaspi ничего не отправлено.')
                return redirect(batch_url(batch['key']))
            if action == 'save' and form:
                if form.is_valid():
                    with transaction.atomic():
                        # Serialize edits through the existing offer; no new tables or migrations.
                        Offer.objects.select_for_update().get(pk=active['offer'].pk)
                        current = Draft.objects.select_for_update().filter(offer=active['offer']).first()
                        stamp = current.updated_at.isoformat() if current else ''
                        if stamp != request.POST.get('draft_stamp', ''):
                            form.add_error(None, 'Карточка изменена в другой вкладке. Обновите страницу.')
                        else:
                            before = {k: getattr(current, k) for k in ('title', 'description', 'photo_url', 'category', 'characteristics')} if current else {}
                            obj = form.save(commit=False)
                            obj.offer = active['offer']
                            obj.updated_by = request.user
                            obj.status = Draft.Status.DRAFT
                            obj.save()
                            Activity.objects.create(user=request.user, action='kaspi_batch_saved', details={
                                'kaspi_batch': batch['key'], 'line': active['index'], 'offer': obj.offer_id,
                                'brand': active['brand'], 'article': active['article'], 'before': before,
                                'after': {k: getattr(obj, k) for k in ('title', 'description', 'photo_url', 'category', 'characteristics')}})
                            messages.success(request, 'Черновик и история изменений сохранены. В Kaspi ничего не отправлено.')
                            return redirect(active['url'] + '#card')
            elif action != 'save' or not form:
                return HttpResponse('Действие недоступно.', status=400)
        event_query = Q(details__kaspi_batch=batch['key'])
        if batch['kind'] == 'order':
            event_query |= Q(details__purchase=batch['obj'].pk, user=request.user)
        events = list(Activity.objects.filter(event_query).select_related('user').order_by('-created_at', '-pk')[:100])
        labels = {'kaspi_batch_prepared': 'Подготовлен черновик', 'kaspi_batch_saved': 'Сохранены изменения карточки',
            'purchase_created': 'Заказ создан', 'purchase_stock_received': 'Принято на склад', 'purchase_listing': 'Зафиксировано состояние продажи'}
        for event in events:
            event.label = labels.get(event.action, 'Изменение закупки')
        batch['source_token'] = token
    elif request.method == 'POST':
        raise Http404
    tab = request.GET.get('tab', 'products')
    if tab not in ('products', 'history', 'files'):
        tab = 'products'
    shown = rows
    state = request.GET.get('state', '')
    if state in ('missing', 'collected'):
        shown = [r for r in rows if r['status_code'] == state]
    term = request.GET.get('q', '').strip()
    if term:
        shown = [r for r in shown if term.casefold() in f"{r['name']} {r['brand']} {r['article']}".casefold()]
    return render(request, 'procurement/kaspi_products.html', context(request, batches=batches, batch=batch,
        rows=shown, active=active, card_form=form, tab=tab, q=term, state=state,
        collected=sum(r['status_code'] == 'collected' for r in rows), missing=sum(r['status_code'] == 'missing' for r in rows),
        files=files, events=events), status=400 if request.method == 'POST' else 200)
