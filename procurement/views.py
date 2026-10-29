import csv
import uuid
from io import BytesIO
from pathlib import PurePath
from copy import deepcopy
from django.core import signing
from django.db import transaction
from functools import wraps
from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db.models import Q, Prefetch, prefetch_related_objects
from django.http import HttpResponse, FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.core.paginator import Paginator
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .analysis import evaluate
from .permissions import workspace_role
from .forms import PreferencesForm, UploadForm, DraftForm, SelectionFilterForm, PurchaseConfirmationForm, PurchaseEditForm, PurchaseLineFormSet
from .importing import import_offers, import_demand, import_enrichment, import_competitors
from .models import Membership, Supplier, Offer, Selection, Preferences, Draft, Activity, Favorite, VehicleFitment, PurchaseOrder, PurchaseDocument


def access(*roles):
    def decorate(view):
        @login_required(login_url='procurement:login')
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            request.procurement_role = workspace_role(request.user)
            if request.procurement_role is None or (roles and request.procurement_role not in roles):
                return render(request, 'procurement/access_denied.html', status=403)
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


def context(request, **extra):
    return {'role': request.procurement_role, 'can_manage': request.procurement_role in ('sales', 'admin'),
            'basket_count': Selection.objects.filter(user=request.user).count(), **extra}


def filtered_rows(request, preferences, filters):
    query = Offer.objects.select_related('supplier', 'kaspi_sellers').all()
    term = filters.get('q', '').strip()
    if term:
        query = query.filter(Q(name__icontains=term) | Q(article__icontains=term) | Q(brand__icontains=term) | Q(applicability__icontains=term))
    if filters.get('supplier'):
        query = query.filter(supplier=filters['supplier'])
    if filters.get('category'):
        query = query.filter(category__in=['Тех обслуживание и сервис', 'Техобслуживание и сервис']) if filters['category'] == 'Тех обслуживание и сервис' else query.filter(category=filters['category'])
    fitment_filters = {'fitments__verified': True}
    if filters.get('vehicle_make'):
        fitment_filters['fitments__make'] = filters['vehicle_make']
    if filters.get('vehicle_model'):
        fitment_filters['fitments__model'] = filters['vehicle_model']
    if filters.get('vehicle_make') or filters.get('vehicle_model') or filters.get('fitment') == 'verified':
        query = query.filter(**fitment_filters).distinct()
    if filters.get('fitment') == 'unknown':
        query = query.exclude(fitments__verified=True)
    if filters.get('max_cost') is not None:
        query = query.filter(cost__lte=filters['max_cost'])
    if filters.get('verified'):
        query = query.filter(match_verified=True)
    favorites = set(Favorite.objects.filter(user=request.user).values_list('offer_id', flat=True))
    if filters.get('favorites'):
        query = query.filter(pk__in=favorites)
    rows = []
    for offer in query:
        row = {'offer': offer, 'favorite': offer.pk in favorites, **evaluate(offer, preferences)}
        if filters.get('availability') and row['stock_status'] != filters['availability']:
            continue
        demand = filters.get('demand')
        if demand == 'reviews' and not row['fresh_reviews']:
            continue
        if demand == 'sales' and not row['confirmed_sales']:
            continue
        if demand == 'unknown' and (row['fresh_reviews'] or row['confirmed_sales']):
            continue
        if any(filters.get(key) is not None and (row[field] is None or row[field] < filters[key])
               for key, field in [('min_profit', 'profit'), ('min_markup', 'markup')]):
            continue
        rows.append(row)
    mode = request.GET.get('mode', 'difference')
    if mode == 'best':
        rows = [r for r in rows if r['markup'] is not None and r['markup'] > 0]
        rows.sort(key=lambda r: (-r['markup'], -r['difference'], r['offer'].pk))
    else:
        rows.sort(key=lambda r: (r['difference'] is not None, r['difference'] or Decimal(0)), reverse=True)
    return rows


@access()
def dashboard(request):
    preferences, _ = Preferences.objects.get_or_create(user=request.user)
    form = SelectionFilterForm(request.GET)
    valid = form.is_valid()
    rows = filtered_rows(request, preferences, form.cleaned_data) if valid else []
    if request.GET.get('download') == 'csv' and valid:
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="procurement-selection.csv"'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        def exported_number(value):
            return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if value is not None else None
        writer.writerow(['Артикул', 'Бренд', 'Наименование', 'Поставщик', 'Закупка, ₸', 'Kaspi, ₸', 'Разница после комиссии, ₸', 'Доходность после комиссии, %', 'Прибыль после комиссии и прочих расходов, ₸', 'Спрос', 'Продажи / 30 дней', 'Найдено отзывов / 90 дней', 'Дата цены', 'Дата проверки спроса', 'Ссылка Kaspi', 'Комиссия без НДС, %', 'Комиссия, ₸', 'Kaspi после комиссии, ₸', 'Прочие расходы, ₸'])
        for row in rows:
            offer = row['offer']
            writer.writerow([safe_csv(x) for x in [offer.article, offer.brand, offer.name, offer.supplier.name,
                exported_number(offer.cost), exported_number(offer.kaspi_price), exported_number(row['difference']),
                exported_number(row['markup']), exported_number(row['profit']), row['signal'], offer.sales_30,
                offer.recent_reviews, offer.checked_on, offer.evidence_date, offer.kaspi_url,
                exported_number(row['commission_rate']), exported_number(row['commission_amount']),
                exported_number(row['kaspi_after_commission']), exported_number(row['expenses'])]])
        return response
    page = Paginator(rows, 40).get_page(request.GET.get('page'))
    prefetch_related_objects([row['offer'] for row in page.object_list],
        Prefetch('fitments', queryset=VehicleFitment.objects.only('id', 'offer_id', 'make', 'model', 'verified')))
    return render(request, 'procurement/dashboard.html', context(request, page=page, total=len(rows), preferences=preferences,
                  commission_rate=preferences.commission.normalize() if preferences.commission is not None else None,
                  filter_form=form, page_numbers=page.paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1), basic_filters=[form[name] for name in ('supplier', 'category', 'vehicle_make', 'vehicle_model', 'fitment', 'availability', 'demand')],
                  advanced_filters=[form[name] for name in ('max_cost', 'min_profit', 'min_markup', 'verified', 'favorites')],
                  mode=request.GET.get('mode', 'difference'), favorites=bool(form.cleaned_data.get('favorites')) if valid else False), status=200 if valid else 400)


@access()
@xframe_options_sameorigin
def detail(request, pk):
    offer = get_object_or_404(Offer.objects.select_related('supplier', 'kaspi_sellers'), pk=pk)
    preferences, _ = Preferences.objects.get_or_create(user=request.user)
    alternatives = Offer.objects.filter(brand__iexact=offer.brand, article__iexact=offer.article).select_related('supplier').order_by('cost')
    return render(request, 'procurement/detail.html', context(request, offer=offer, analysis=evaluate(offer, preferences), alternatives=alternatives))


@access()
@require_POST
def favorite(request, pk):
    offer = get_object_or_404(Offer, pk=pk)
    action = request.POST.get('action')
    if action == 'add':
        Favorite.objects.get_or_create(user=request.user, offer=offer)
    elif action == 'remove':
        Favorite.objects.filter(user=request.user, offer=offer).delete()
    else:
        return HttpResponse('Неизвестное действие.', status=400)
    target = request.POST.get('next', '')
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        target = reverse('procurement:dashboard')
    return redirect(target)


@access()
def settings(request):
    preferences, _ = Preferences.objects.get_or_create(user=request.user)
    form = PreferencesForm(request.POST or None, instance=preferences)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Параметры расчёта сохранены.')
        return redirect('procurement:dashboard')
    return render(request, 'procurement/form.html', context(request, form=form, title='Алгоритм и настройки', help='Комиссия Kaspi указывается без НДС и применяется к цене продажи. Разница после комиссии = Kaspi × (1 − комиссия / 100) − закупка. Доходность после комиссии = эта разница / закупка × 100%. Чистая прибыль = разница после комиссии − все прочие расходы на единицу; комиссия повторно не вычитается. Входные цены по НДС не пересчитываются. Ставка и расходы берутся из ваших личных настроек. Принцип заполнения вкладки «Самые выгодные к закупке»: известные закупочная цена больше нуля, цена Kaspi и комиссия, положительная доходность закупки. Сортировка строго по доходности закупки по убыванию, при равной — по заработку после комиссии в тенге по убыванию. Спрос, отзывы, совпадение с Kaspi, наличие и даты показаны справочно: отсутствие или устаревание этих данных не исключает товар и не понижает его позицию. Прочие расходы и пороги минимальной прибыли и доходности из личных настроек не ограничивают этот рейтинг. Дополнительные фильтры каталога применяются только при вашем явном выборе. CSV повторяет состав и порядок списка. Доходность рассчитана после комиссии, до прочих расходов; чистая прибыль показана отдельно. Отзывы не равны продажам. Неизвестные комиссия и расходы не считаются нулевыми.'))


@access('sales', 'admin')
def upload(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        if form.cleaned_data['checked_on'] > timezone.localdate():
            form.add_error('checked_on', 'Дата не может быть в будущем.')
        else:
            try:
                handler = {'demand': import_demand, 'catalog': import_offers, 'enrichment': import_enrichment, 'competitors': import_competitors}[form.cleaned_data['kind']]
                count = handler(form.cleaned_data['file'], form.cleaned_data['supplier'], form.cleaned_data['checked_on'], request.user)
            except Exception as error:
                # Import transaction rolls back completely; never expose file contents or credentials.
                form.add_error(None, str(error) if isinstance(error, ValueError) else 'Файл не прочитан. Проверьте формат и структуру.')
            else:
                messages.success(request, f'Загружено товаров: {count}.')
                return redirect('procurement:dashboard')
    return render(request, 'procurement/form.html', context(request, form=form, title='Загрузка данных', help='Продавцы Kaspi: JSON с checked_on и offers (brand, article, source_url, city, complete, sellers). Цены самого предложения и спрос не изменяются. Применимость Omega: JSON с датой checked_on и массивом offers (brand, article, source_url, category, fitments). Импорт применимости не изменяет цены и спрос. Прайс: article, brand, name, cost; необязательно category, stock, min_order, kaspi_price, kaspi_url, supplier_url. Поддерживается исходный Excel Omega. Отчёт спроса: article, brand, sales_30, review_count, recent_reviews, last_review (YYYY-MM-DD). Неизвестные значения оставьте пустыми. Для sales_30 обязательны period_start и period_end: ровно 30 календарных дней включительно до указанной даты. Отчёт спроса должен быть приведён к этим колонкам; автоматическое чтение любого формата Kaspi пока не поддерживается. Импорт атомарный.'))


@access()
@require_POST
def select(request, pk):
    offer = get_object_or_404(Offer, pk=pk)
    as_json = 'application/json' in request.headers.get('Accept', '')
    def invalid_quantity(message):
        return JsonResponse({'error': message}, status=400) if as_json else HttpResponse(message, status=400)
    try:
        quantity = int(request.POST.get('quantity', offer.min_order))
    except (ValueError, TypeError):
        return invalid_quantity('Количество должно быть целым.')
    if quantity < offer.min_order or quantity > 100000:
        return invalid_quantity('Количество вне допустимого диапазона.')
    Selection.objects.update_or_create(user=request.user, offer=offer, defaults={'quantity': quantity})
    if as_json:
        return JsonResponse({'basket_count': Selection.objects.filter(user=request.user).count(),
                             'message': 'Добавлено в список закупки.'})
    target = request.POST.get('next', '')
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        messages.success(request, 'Добавлено в список закупки.')
        return redirect(target)
    return redirect('procurement:basket')


@access()
@require_POST
def remove(request, pk):
    Selection.objects.filter(pk=pk, user=request.user).delete()
    return redirect('procurement:basket')


def purchase_snapshot(rows):
    return [{'offer_id': row.offer_id, 'selection_id': row.pk, 'brand': row.offer.brand,
             'article': row.offer.article, 'name': row.offer.name, 'quantity': row.quantity,
             'cost': str(row.offer.cost), 'subtotal': str(row.quantity * row.offer.cost),
             'supplier_url': row.offer.supplier_url} for row in rows]


@access()
def basket(request):
    rows = list(Selection.objects.filter(user=request.user).select_related('offer__supplier').order_by('offer__supplier__name', 'pk'))
    grouped = {}
    for row in rows:
        row.subtotal = row.quantity * row.offer.cost
        group = grouped.setdefault(row.offer.supplier_id, {'supplier': row.offer.supplier, 'rows': [], 'total': Decimal(0)})
        group['rows'].append(row)
        group['total'] += row.subtotal
    orders = PurchaseOrder.objects.filter(user=request.user).select_related('supplier')
    return render(request, 'procurement/basket.html', context(request, selections=rows, supplier_groups=list(grouped.values()),
        purchases=orders, budget=sum((r.subtotal for r in rows), Decimal(0))))


@access()
def purchase_confirm(request, supplier_id):
    supplier = get_object_or_404(Supplier, pk=supplier_id)
    def selection_rows(lock=False):
        query = Selection.objects.filter(user=request.user, offer__supplier=supplier).select_related('offer').order_by('pk')
        return list(query.select_for_update(of=('self',)) if lock else query)
    rows = selection_rows()
    form = PurchaseConfirmationForm(request.POST or None, initial={'request_key': uuid.uuid4(),
        'selection_token': signing.dumps(purchase_snapshot(rows), salt='procurement-purchase')})
    if request.method == 'POST' and form.is_valid():
        existing = PurchaseOrder.objects.filter(user=request.user, request_key=form.cleaned_data['request_key']).first()
        if existing:
            return redirect('procurement:purchase_detail', pk=existing.pk)
        try:
            reviewed = signing.loads(form.cleaned_data['selection_token'], salt='procurement-purchase', max_age=3600)
        except signing.BadSignature:
            reviewed = None
        with transaction.atomic():
            rows = selection_rows(lock=True)
            snapshot = purchase_snapshot(rows)
            if not rows or reviewed != snapshot:
                form.add_error(None, 'Список или цены изменились либо подтверждение устарело. Откройте подтверждение заново.')
            else:
                order = PurchaseOrder.objects.create(user=request.user, supplier=supplier,
                    request_key=form.cleaned_data['request_key'], supplier_order_number=form.cleaned_data['supplier_order_number'],
                    comment=form.cleaned_data['comment'], items=snapshot, original_items=deepcopy(snapshot), estimated_total=sum((row.quantity * row.offer.cost for row in rows), Decimal(0)))
                Selection.objects.filter(pk__in=[row.pk for row in rows], user=request.user).delete()
                Activity.objects.create(user=request.user, action='purchase_created', details={'purchase': order.pk, 'supplier': supplier.pk, 'positions': len(rows)})
                messages.success(request, 'Закупочный заказ создан в ZPT.KZ. Теперь его можно выгрузить в Excel.')
                return redirect('procurement:purchase_detail', pk=order.pk)
    return render(request, 'procurement/purchase_confirm.html', context(request, supplier=supplier, form=form,
        selections=rows, budget=sum((row.quantity * row.offer.cost for row in rows), Decimal(0))), status=400 if request.method == 'POST' else 200)


@access()
def purchase_detail(request, pk):
    order = get_object_or_404(PurchaseOrder.objects.select_related('supplier'), pk=pk, user=request.user)
    form = PurchaseEditForm(request.POST or None, request.FILES or None, instance=order, initial={'version': order.version})
    lines = PurchaseLineFormSet(request.POST or None, prefix='lines', initial=[{'quantity': item['quantity'], 'cost': item['cost']} for item in order.items])
    if request.method == 'POST':
        valid = form.is_valid() and lines.is_valid()
        if valid and len(lines.forms) != len(order.items):
            form.add_error(None, 'Состав заказа изменился. Обновите страницу.')
            valid = False
        if valid:
            with transaction.atomic():
                current = PurchaseOrder.objects.select_for_update().get(pk=order.pk, user=request.user)
                if form.cleaned_data['version'] != current.version:
                    form.add_error(None, 'Заказ изменён в другой вкладке. Обновите страницу перед сохранением.')
                else:
                    fields = ['status', 'supplier_order_number', 'invoice_number', 'waybill_number', 'comment']
                    before = {field: getattr(current, field) for field in fields}
                    before['items'] = deepcopy(current.items)
                    updated_items = deepcopy(current.items)
                    for item, line in zip(updated_items, lines.cleaned_data):
                        item['quantity'] = line['quantity']
                        item['cost'] = str(line['cost'])
                        item['subtotal'] = str(line['quantity'] * line['cost'])
                    for field in fields:
                        setattr(current, field, form.cleaned_data[field])
                    current.items = updated_items
                    current.version += 1
                    current.save(update_fields=fields + ['items', 'version', 'updated_at'])
                    for field, kind in [('invoice_file', 'invoice'), ('waybill_file', 'waybill')]:
                        file = form.cleaned_data.get(field)
                        if file:
                            PurchaseDocument.objects.create(order=current, kind=kind, name=PurePath(file.name).name[:255], data=file.read())
                    Activity.objects.create(user=request.user, action='purchase_updated', details={'purchase': current.pk, 'before': before, 'version': current.version})
                    messages.success(request, 'Изменения закупочного заказа сохранены.')
                    return redirect('procurement:purchase_detail', pk=current.pk)
    return render(request, 'procurement/purchase_detail.html', context(request, purchase=order, form=form, lines=lines,
        item_forms=list(zip(order.items, lines.forms)), documents=order.documents.defer('data').all()), status=400 if request.method == 'POST' else 200)


@access()
def purchase_excel(request, pk):
    from .purchase_excel import export_order
    order = get_object_or_404(PurchaseOrder.objects.select_related('supplier'), pk=pk, user=request.user)
    return FileResponse(export_order(order), as_attachment=True, filename=f'ZK-{order.pk:06d}.xlsx',
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@access()
def purchase_document(request, pk, document_id):
    document = get_object_or_404(PurchaseDocument.objects.select_related('order'), pk=document_id, order_id=pk, order__user=request.user)
    response = FileResponse(BytesIO(bytes(document.data)), as_attachment=True, filename=document.name, content_type='application/octet-stream')
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def safe_csv(value):
    if isinstance(value, (Decimal, int)):
        return str(value)
    text = str(value if value is not None else '')
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


@access()
def export(request):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="procurement.csv"'
    response.write('\ufeff')
    writer = csv.writer(response, delimiter=';')
    writer.writerow(['Поставщик', 'Артикул', 'Бренд', 'Название', 'Количество', 'Закупка', 'Бюджет', 'Kaspi'])
    for row in Selection.objects.filter(user=request.user).select_related('offer__supplier'):
        writer.writerow([safe_csv(v) for v in [row.offer.supplier.name, row.offer.article, row.offer.brand, row.offer.name, row.quantity, row.offer.cost, row.quantity * row.offer.cost, row.offer.kaspi_url]])
    return response


@access('sales', 'admin')
def draft(request, pk):
    offer = get_object_or_404(Offer, pk=pk)
    existing = Draft.objects.filter(offer=offer).first()
    form = DraftForm(request.POST or None, instance=existing, initial={'title': offer.name, 'description': offer.description, 'photo_url': offer.photo_url, 'category': offer.category, 'characteristics': offer.characteristics})
    if request.method == 'POST' and form.is_valid():
        item = form.save(commit=False)
        item.offer = offer
        item.updated_by = request.user
        item.save()
        Activity.objects.create(user=request.user, action='draft_saved', details={'offer': offer.pk})
        messages.success(request, 'Черновик сохранён. В Kaspi ничего не отправлено.')
        return redirect('procurement:detail', pk=pk)
    return render(request, 'procurement/form.html', context(request, form=form, title='Подготовка карточки Kaspi', help='Сохранение создаёт черновик. Публикация и проверка обязательных полей категории будут доступны после настройки интеграции. Не добавляйте неподтверждённые характеристики.'))


@access('sales', 'admin')
@require_POST
def verify(request, pk):
    offer = get_object_or_404(Offer, pk=pk)
    if request.POST.get('confirmed') != 'yes' or not offer.kaspi_url:
        return HttpResponse('Подтвердите бренд, артикул, комплектность и применимость карточки.', status=400)
    offer.match_verified = True
    offer.save(update_fields=['match_verified'])
    Activity.objects.create(user=request.user, action='match_verified', details={'offer': offer.pk})
    return redirect('procurement:detail', pk=pk)


@access('admin')
def users(request):
    if request.method == 'POST':
        user = get_object_or_404(get_user_model(), pk=request.POST.get('user_id') if request.POST.get('user_id', '').isdigit() else 0)
        role = request.POST.get('role')
        if user == request.user or user.is_superuser or role not in [*Membership.Role.values, 'disabled']:
            return HttpResponse('Недопустимое изменение роли.', status=400)
        defaults = {'active': False} if role == 'disabled' else {'role': role, 'active': True}
        membership, _ = Membership.objects.update_or_create(user=user, defaults=defaults)
        Activity.objects.create(user=request.user, action='role_changed', details={'user_id': user.pk, 'role': role})
        messages.success(request, 'Роль сохранена. Глобальные права пользователя не изменены.')
        return redirect('procurement:users')
    return render(request, 'procurement/users.html', context(request, members=Membership.objects.select_related('user'), accounts=get_user_model().objects.filter(is_active=True, is_superuser=False).exclude(pk=request.user.pk).order_by('username'), roles=[*Membership.Role.choices, ('disabled', 'Отключить доступ к закупкам')]))
