from .catalogue_import import import_catalogue_fitments
from .shared_fitments import fill_from_shared_base
from .catalog_paging import catalog_page, best_candidates_query
from .fitment_filters import matches_configuration
from .supplier_terms import supplier_terms
from .vehicle_countries import country_makes
import csv
import logging
from time import perf_counter
import uuid
from io import BytesIO
from pathlib import PurePath
from copy import deepcopy
from datetime import timedelta
from django.core import signing
from django.db import transaction
from functools import wraps
from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db.models import Q, Prefetch, prefetch_related_objects, Count, F
from django.http import HttpResponse, FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.core.paginator import Paginator
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .analysis import evaluate, MIN_BEST_PROFIT, PACKAGING_COST
from .permissions import workspace_role
from .forms import PreferencesForm, UploadForm, DraftForm, SelectionFilterForm, PurchaseConfirmationForm, PurchaseEditForm, PurchaseLineFormSet
from .importing import import_offers, import_demand, import_enrichment, import_competitors, import_matches, import_card_searches
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


def request_supplier(request):
    # An explicit tab URL takes precedence over another tab's last selection.
    supplier_id = request.GET.get('supplier', request.session.get('procurement_supplier'))
    if not supplier_id or not str(supplier_id).isdigit() or len(str(supplier_id)) > 18:
        return None
    return Supplier.objects.filter(pk=supplier_id).first()


def context(request, **extra):
    selected_supplier = extra.get('supplier') or getattr(extra.get('offer'), 'supplier', None) or request_supplier(request)
    catalog_url = reverse('procurement:dashboard')
    scope_query = '?supplier='
    if selected_supplier:
        scope_query += str(selected_supplier.pk)
    catalog_url += scope_query
    return {'role': request.procurement_role, 'can_manage': request.procurement_role in ('sales', 'admin'),
            'selected_supplier': selected_supplier, 'catalog_url': catalog_url,
            'favorites_url': catalog_url + '&favorites=1',
            'upload_url': reverse('procurement:upload') + scope_query,
            'settings_url': reverse('procurement:settings') + scope_query,
            'basket_url': reverse('procurement:basket') + scope_query,
            'basket_count': Selection.objects.filter(user=request.user).count(), **extra}


def kaspi_status(offer, preferences, today):
    snapshot = getattr(offer, 'kaspi_sellers', None)
    search = getattr(offer, 'kaspi_card_search', None)
    fresh = today - timedelta(days=preferences.no_sellers_days)
    code = 'pending'
    if snapshot and offer.kaspi_url and snapshot.source_url == offer.kaspi_url and snapshot.city == '750000000' and fresh <= snapshot.checked_on <= today:
        if snapshot.sellers:
            code = 'sellers'
        elif snapshot.complete and offer.match_verified:
            code = 'empty'
    if not offer.kaspi_url and search and search.complete and search.city == '750000000' and fresh <= search.checked_on <= today and (search.checked_brand, search.checked_article, search.checked_name) == (offer.brand, offer.article, offer.name):
        code = 'missing'
    labels = {'sellers': 'Карточка есть — есть продавцы Kaspi', 'empty': 'Карточка есть — нет продавцов Kaspi', 'missing': 'Карточка Kaspi не найдена', 'pending': 'Проверка Kaspi не завершена'}
    return {'kaspi_status_code': code, 'kaspi_status': labels[code], 'kaspi_checked_on': max((x.checked_on for x in (snapshot, search) if x), default=None)}


def filtered_rows(request, preferences, filters, paginate=False):
    # Ranking uses scalar offer facts; detailed descriptions/JSON and seller lists
    # are not needed for every item in the supplier catalogue.
    query = Offer.objects.select_related('supplier', 'kaspi_sellers', 'kaspi_card_search').defer('description', 'characteristics', 'supplier_url')
    mode = request.GET.get('mode', 'roi')
    today = timezone.localdate()
    if mode == 'best':
        filters = {key: value for key, value in filters.items() if key not in ('kaspi_status', 'verified', 'sort')}
    if mode == 'best':
        query = best_candidates_query(query, preferences, today)
    elif mode == 'reviewed':
        pass  # Legacy URL now opens the complete supplier catalogue.
    elif mode == 'no_sellers':
        fresh = today - timedelta(days=preferences.no_sellers_days)
        empty_card = Q(match_verified=True, kaspi_sellers__complete=True,
            kaspi_sellers__sellers=[], kaspi_sellers__city='750000000',
            kaspi_sellers__source_url=F('kaspi_url'),
            kaspi_sellers__checked_on__gte=fresh, kaspi_sellers__checked_on__lte=today) & ~Q(kaspi_url='')
        no_card = Q(kaspi_url='', kaspi_card_search__complete=True,
            kaspi_card_search__city='750000000', kaspi_card_search__checked_on__gte=fresh,
            kaspi_card_search__checked_on__lte=today, kaspi_card_search__checked_brand=F('brand'),
            kaspi_card_search__checked_article=F('article'), kaspi_card_search__checked_name=F('name'))
        presence = request.GET.get('kaspi_presence')
        query = query.select_related('kaspi_sellers', 'kaspi_card_search').filter(
            no_card if presence == 'no_card' else empty_card if presence == 'has_card' else empty_card | no_card)
    bounds = filters.get('seller_bounds')
    if bounds is not None:
        low, high = bounds
        query = query.filter(kaspi_sellers__complete=True, kaspi_sellers__city='750000000',
            kaspi_sellers__source_url=F('kaspi_url'),
            kaspi_sellers__checked_on__gte=today - timedelta(days=preferences.no_sellers_days),
            kaspi_sellers__checked_on__lte=today).exclude(kaspi_url='')
        # Array indices implement inclusive bounds in SQLite and PostgreSQL,
        # retaining SQL pagination instead of loading all seller lists.
        query = query.filter(**{f'kaspi_sellers__sellers__{high}__isnull': True})
        if low > 0:
            query = query.filter(**{f'kaspi_sellers__sellers__{low - 1}__isnull': False})
        else:
            query = query.filter(~Q(kaspi_sellers__sellers=[]) | Q(match_verified=True))
    term = filters.get('q', '').strip()
    if term:
        query = query.filter(Q(name__icontains=term) | Q(article__icontains=term) | Q(brand__icontains=term) | Q(applicability__icontains=term))
    if filters.get('supplier'):
        query = query.filter(supplier=filters['supplier'])
    if filters.get('brand'):
        query = query.filter(brand__icontains=filters['brand'])
    if filters.get('category'):
        query = query.filter(category__in=['Тех обслуживание и сервис', 'Техобслуживание и сервис']) if filters['category'] == 'Тех обслуживание и сервис' else query.filter(category=filters['category'])
    fitment_filters = {'fitments__verified': True}
    if filters.get('vehicle_country'):
        fitment_filters['fitments__make__in'] = country_makes(filters['vehicle_country'].pk)
    if filters.get('vehicle_make'):
        fitment_filters['fitments__make'] = filters['vehicle_make']
    if filters.get('vehicle_model'):
        fitment_filters['fitments__model'] = filters['vehicle_model']
    if filters.get('vehicle_country') or filters.get('vehicle_make') or filters.get('vehicle_model') or filters.get('fitment') == 'verified':
        query = query.filter(**fitment_filters).distinct()
    if filters.get('vehicle_generation') or filters.get('vehicle_year') or filters.get('selected_years'):
        fitments = VehicleFitment.objects.filter(offer__in=query.values('pk'), **{key.removeprefix('fitments__'): value for key, value in fitment_filters.items()})
        eligible = {fitment.offer_id for fitment in fitments.only('offer_id', 'configurations').iterator(chunk_size=20)
                    if matches_configuration(fitment.configurations, filters.get('vehicle_generation', ''), filters.get('vehicle_year'), filters.get('selected_years'))}
        query = query.filter(pk__in=eligible)
    if filters.get('fitment') == 'unknown':
        query = query.exclude(fitments__verified=True)
    if filters.get('max_cost') is not None:
        query = query.filter(cost__lte=filters['max_cost'])
    if filters.get('verified'):
        query = query.filter(match_verified=True)
    favorites = set(Favorite.objects.filter(user=request.user).values_list('offer_id', flat=True))
    if filters.get('favorites'):
        query = query.filter(pk__in=favorites)
    if paginate and mode in ('roi', 'reviewed') and not any(filters.get(key) for key in ('availability', 'demand')) and all(filters.get(key) is None for key in ('min_profit', 'min_markup')):
        request.catalog_page, request.catalog_status_counts = catalog_page(query, preferences, filters, mode, request.GET.get('page'), today)
        request.catalog_total = request.catalog_page.paginator.count
        query = request.catalog_page.object_list
    rows = []
    for offer in query:
        row = {'offer': offer, 'favorite': offer.pk in favorites, **evaluate(offer, preferences, today=today)}
        row.update(kaspi_status(offer, preferences, today))
        row['kaspi_price'] = offer.kaspi_price
        if row['kaspi_status_code'] in ('empty', 'missing'):
            row.update({key: None for key in ('kaspi_price', 'difference', 'markup', 'profit', 'margin', 'commission_amount', 'kaspi_after_commission')})
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
    if mode == 'best':
        request.best_candidates = [r for r in rows if r['offer'].match_verified
            and r['stock_status'] == 'in_stock' and r['supplier_fresh'] and r['price_fresh']
            and r['profit'] is not None and r['profit'] >= max(MIN_BEST_PROFIT, preferences.best_min_profit)
            and r['kaspi_status_code'] not in ('empty', 'missing')]
        for candidate in request.best_candidates:
            candidate['markup'] = candidate['profit'] / candidate['offer'].cost * 100
        request.best_candidates.sort(key=lambda r: (-r['markup'], -r['profit'], r['offer'].pk))
        rows = [r for r in rows if r['markup'] is not None and r['markup'] > 0
                and r['profit'] is not None and r['profit'] >= max(MIN_BEST_PROFIT, preferences.best_min_profit)
                and r['offer'].match_verified and r['stock_status'] == 'in_stock'
                and r['supplier_fresh'] and r['price_fresh'] and r['confirmed_sales'] and r['kaspi_status_code'] == 'sellers']
        for row in rows:
            row['markup'] = row['profit'] / row['offer'].cost * 100
        rows.sort(key=lambda r: (-r['markup'], -r['profit'], r['offer'].pk))
    elif mode == 'reviewed':
        rows.sort(key=lambda r: (r['markup'] is None, -(r['markup'] or Decimal(0)), r['offer'].pk))
    elif mode == 'difference':
        if preferences.difference_min_profit is not None:
            rows = [r for r in rows if r['profit'] is not None and r['profit'] >= preferences.difference_min_profit]
        rows.sort(key=lambda r: (r['profit'] is None, -(r['profit'] or Decimal(0)),
                                r['difference'] is None, -(r['difference'] or Decimal(0)), r['offer'].pk))
    elif mode == 'no_sellers':
        eligible = []
        for row in rows:
            if row['stock_status'] != 'in_stock':
                continue
            offer = row['offer']
            search = getattr(offer, 'kaspi_card_search', None)
            if not offer.kaspi_url and search:
                row['kaspi_presence'] = 'no_card'
                row['kaspi_checked_on'] = search.checked_on
            else:
                row['kaspi_presence'] = 'has_card'
                row['kaspi_checked_on'] = offer.kaspi_sellers.checked_on
            eligible.append(row)
        rows = eligible
        rows.sort(key=lambda r: (-int(r['confirmed_sales']), -int(r['fresh_reviews']),
                                -r['kaspi_checked_on'].toordinal(), r['offer'].pk))
    else:
        rows.sort(key=lambda r: (r['markup'] is None, -(r['markup'] or Decimal(0)),
                                 -(r['difference'] or Decimal(0)), r['offer'].pk))
    if getattr(request, 'catalog_status_counts', None) is None:
        request.catalog_status_counts = {key: sum(r['kaspi_status_code'] == key for r in rows) for key in ('sellers', 'empty', 'missing', 'pending')}
    if filters.get('kaspi_status'):
        rows = [r for r in rows if r['kaspi_status_code'] in filters['kaspi_status']]
    if filters.get('sort') == 'profit' and mode != 'best':
        rows.sort(key=lambda r: (r['profit'] is None, -(r['profit'] or Decimal(0)), r['offer'].pk))
    if hasattr(request, 'catalog_page'):
        request.catalog_page.object_list = rows
    return rows


@access()
def dashboard(request):
    if 'supplier' not in request.GET and request.session.get('procurement_supplier'):
        params = request.GET.copy()
        params['supplier'] = request.session['procurement_supplier']
        return redirect(reverse('procurement:dashboard') + '?' + params.urlencode())
    started = perf_counter()
    preferences, _ = Preferences.objects.get_or_create(user=request.user)
    filter_data = request.GET
    if request.GET.get('mode') == 'best':
        filter_data = request.GET.copy()
        for key in ('kaspi_status', 'verified', 'sort'):
            filter_data.pop(key, None)
    form = SelectionFilterForm(filter_data)
    valid = form.is_valid()
    form_finished = perf_counter()
    if valid:
        supplier = form.cleaned_data.get('supplier')
        request.session['procurement_supplier'] = supplier.pk if supplier else None
    else:
        supplier = form.cleaned_data.get('supplier')
    supplier_stats = None
    if supplier:
        supplier_stats = {'loaded': Offer.objects.filter(supplier=supplier).count()}
    rows = filtered_rows(request, preferences, form.cleaned_data, paginate=request.GET.get('download') != 'csv') if valid else []
    rows_finished = perf_counter()
    best_empty_reason = ''
    if request.GET.get('mode') == 'best' and not rows:
        scope = Offer.objects.filter(supplier=supplier) if supplier else Offer.objects.all()
        if not scope.filter(sales_30__gt=0, evidence_date__gte=timezone.localdate() - timedelta(days=7), evidence_date__lte=timezone.localdate()).exists():
            best_empty_reason = 'У поставщика нет товаров со свежими подтверждёнными продажами за 30 дней. Для подборки нужен загруженный отчёт о продажах; отзывы Kaspi его не заменяют.'
        else:
            best_empty_reason = 'Пока ни один товар не соответствует всем условиям алгоритма одновременно. Проверьте остатки, совпадения, даты цен, продавцов Kaspi и прибыль после расходов.'
    counts = getattr(request, 'catalog_status_counts', {})
    form.fields['kaspi_status'].choices = [(key, f'{label} ({counts.get(key, 0)})') for key, label in form.fields['kaspi_status'].choices]
    preliminary = request.GET.get('mode') == 'best' and not rows and bool(getattr(request, 'best_candidates', []))
    displayed_rows = request.best_candidates if preliminary else rows
    if request.GET.get('download') == 'csv' and valid:
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="procurement-selection.csv"'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        def exported_number(value):
            return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if value is not None else None
        writer.writerow(['Артикул', 'Бренд', 'Наименование', 'Поставщик', 'Закупка, ₸', 'Kaspi, ₸', 'Разница после комиссии, ₸', 'Доходность после комиссии, %', 'Прибыль после комиссии и прочих расходов, ₸', 'Спрос', 'Продажи / 30 дней', 'Найдено отзывов / 90 дней', 'Дата цены', 'Дата проверки спроса', 'Ссылка Kaspi', 'Комиссия без НДС, %', 'Комиссия, ₸', 'Kaspi после комиссии, ₸', 'Прочие расходы, ₸', 'Статус подбора'])
        for row in displayed_rows:
            offer = row['offer']
            if request.GET.get('mode') == 'no_sellers':
                row = {**row, **{key: None for key in ('difference', 'markup', 'profit', 'commission_amount', 'kaspi_after_commission')}}
            writer.writerow([safe_csv(x) for x in [offer.article, offer.brand, offer.name, offer.supplier.name,
                exported_number(offer.cost), exported_number(row['kaspi_price']) if request.GET.get('mode') != 'no_sellers' else None, exported_number(row['difference']),
                exported_number(row['markup']), exported_number(row['profit']), row['signal'], offer.sales_30,
                offer.recent_reviews, offer.checked_on, offer.evidence_date, offer.kaspi_url,
                exported_number(row['commission_rate']), exported_number(row['commission_amount']),
                exported_number(row['kaspi_after_commission']), exported_number(row['expenses']), 'Предварительная подборка — требует проверки спроса и продавцов Kaspi' if preliminary else 'Основная подборка']])
        return response
    page = getattr(request, 'catalog_page', None) or Paginator(displayed_rows, 40).get_page(request.GET.get('page'))
    prefetch_related_objects([row['offer'] for row in page.object_list],
        Prefetch('fitments', queryset=VehicleFitment.objects.only('id', 'offer_id', 'make', 'model', 'verified')[:4], to_attr='compact_fitments'),
        'kaspi_sellers')
    response = render(request, 'procurement/dashboard.html', context(request, page=page, total=getattr(request, 'catalog_total', len(rows)), preferences=preferences,
                  commission_rate=preferences.commission.normalize() if preferences.commission is not None else None,
                  effective_expenses=max(PACKAGING_COST, preferences.expenses) if preferences.expenses is not None else None,
                  filter_form=form, preliminary=preliminary, preliminary_total=len(displayed_rows) if preliminary else 0, best_empty_reason=best_empty_reason, supplier_stats=supplier_stats, catalogue_total=supplier_stats['loaded'] if supplier_stats else Offer.objects.count(),
                  reset_url=reverse('procurement:dashboard') + (f'?supplier={supplier.pk}' if supplier else '?supplier=') + ('&mode=' + request.GET['mode'] if request.GET.get('mode') in ('best', 'difference', 'no_sellers', 'reviewed') else ''),
                  page_numbers=page.paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1), basic_filters=[form[name] for name in ('brand', 'category', 'vehicle_country', 'vehicle_make', 'vehicle_model', 'vehicle_generation', 'vehicle_year', 'kaspi_seller_count')],
                  advanced_filters=[form[name] for name in (('sort', 'fitment', 'availability', 'demand', 'max_cost', 'min_profit', 'min_markup', 'verified', 'favorites') if request.GET.get('mode') != 'best' else ('fitment', 'availability', 'demand', 'max_cost', 'min_profit', 'min_markup', 'favorites'))],
                  mode=request.GET.get('mode', 'roi'), favorites=bool(form.cleaned_data.get('favorites')) if valid else False), status=200 if valid else 400)
    finished = perf_counter()
    durations = [(form_finished-started)*1000, (rows_finished-form_finished)*1000, (finished-rows_finished)*1000]
    response['Server-Timing'] = ', '.join(f'{name};dur={duration:.1f}' for name, duration in zip(('filters', 'catalogue', 'render'), durations))
    logging.getLogger('gunicorn.error').info('Procurement performance: mode=%s filters=%.1fms catalogue=%.1fms render=%.1fms',
        'best' if request.GET.get('mode') == 'best' else 'catalogue', *durations)
    return response



@access()
@xframe_options_sameorigin
def detail(request, pk):
    offer = get_object_or_404(Offer.objects.select_related('supplier', 'kaspi_sellers', 'kaspi_card_search'), pk=pk)
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
        supplier = request_supplier(request)
        return redirect(reverse('procurement:dashboard') + '?supplier=' + (str(supplier.pk) if supplier else ''))
    return render(request, 'procurement/form.html', context(request, form=form, title='Алгоритм и настройки', help='Настройки каждого режима сохраняются отдельно для вашего аккаунта. Комиссия берётся из личных настроек, комиссия повторно не вычитается; расходы включают упаковку 250 ₸ один раз. Прибыль = цена продажи после комиссии − закупка − расходы. Доходность = (цена после комиссии − закупка) / закупка × 100%. Неизвестные значения не заменяются нулём. Совпадение проверяется по артикулу, названию и комплектности, затем бренду. Скидки поставщика справочные до подтверждения условий. CSV повторяет отбор и порядок списка.'))


@access('sales', 'admin')
def upload(request):
    supplier = request_supplier(request)
    form = UploadForm(request.POST or None, request.FILES or None,
                      initial={'supplier': supplier.name if supplier else ''})
    if request.method == 'POST' and form.is_valid():
        if form.cleaned_data['checked_on'] > timezone.localdate():
            form.add_error('checked_on', 'Дата не может быть в будущем.')
        else:
            try:
                handler = {'demand': import_demand, 'catalog': import_offers, 'enrichment': import_enrichment, 'catalogue_fitments': import_catalogue_fitments, 'competitors': import_competitors, 'matches': import_matches, 'card_searches': import_card_searches}[form.cleaned_data['kind']]
                count = handler(form.cleaned_data['file'], form.cleaned_data['supplier'], form.cleaned_data['checked_on'], request.user)
            except Exception as error:
                # Import transaction rolls back completely; never expose file contents or credentials.
                form.add_error(None, str(error) if isinstance(error, ValueError) else 'Файл не прочитан. Проверьте формат и структуру.')
            else:
                messages.success(request, f'Загружено товаров: {count}.')
                imported_supplier = Supplier.objects.get(name=form.cleaned_data['supplier'].strip())
                request.session['procurement_supplier'] = imported_supplier.pk
                return redirect(reverse('procurement:dashboard') + f'?supplier={imported_supplier.pk}')
    return render(request, 'procurement/form.html', context(request, form=form, title='Загрузка данных', help='Каталожная применимость: JSON с checked_on и offers (brand, article, source_brand, source_article, source_url, status=sourced, rows с make, model, years, engine, notes). Сохраняются источники и ограничения; уже заполненные записи не перезаписываются. Продавцы Kaspi: JSON с checked_on и offers (brand, article, source_url, city, complete, sellers). Цены самого предложения и спрос не изменяются. Применимость Omega: JSON с датой checked_on и массивом offers (brand, article, source_url, category, fitments). Импорт применимости не изменяет цены и спрос. Прайс: article, brand, name, cost; необязательно category, stock, min_order, kaspi_price, kaspi_url, supplier_url. Поддерживается исходный Excel Omega. Отчёт спроса: article, brand, sales_30, review_count, recent_reviews, last_review (YYYY-MM-DD). Неизвестные значения оставьте пустыми. Для sales_30 обязательны period_start и period_end: ровно 30 календарных дней включительно до указанной даты. Отчёт спроса должен быть приведён к этим колонкам; автоматическое чтение любого формата Kaspi пока не поддерживается. Импорт атомарный.'))


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
    from .purchase_history import supplier_history
    for group in grouped.values():
        history=supplier_history(request.user,group['supplier'].pk)
        for row in group['rows']:row.purchase_history=history[(row.offer.article,row.offer.brand)]
        group['terms'] = supplier_terms(group['supplier'], group['total'])
    orders = PurchaseOrder.objects.filter(user=request.user,archived=False).select_related('supplier')
    from inventory.purchasing import order_summary
    for order in orders:order.workflow=order_summary(order)
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
    from .purchase_history import supplier_history
    history=supplier_history(request.user,supplier.pk)
    for row in rows:row.purchase_history=history[(row.offer.article,row.offer.brand)]
    return render(request, 'procurement/purchase_confirm.html', context(request, supplier=supplier, form=form,
        selections=rows, terms=supplier_terms(supplier, sum((row.quantity * row.offer.cost for row in rows), Decimal(0))), budget=sum((row.quantity * row.offer.cost for row in rows), Decimal(0))), status=400 if request.method == 'POST' else 200)


@access()
def purchase_detail(request, pk):
    order = get_object_or_404(PurchaseOrder.objects.select_related('supplier'), pk=pk, user=request.user)
    form = PurchaseEditForm(request.POST or None, request.FILES or None, instance=order, initial={'version': order.version})
    lines = PurchaseLineFormSet(request.POST or None, prefix='lines', initial=[{'quantity': item['quantity'], 'cost': item['cost']} for item in order.items])
    has_receipts = order.stock_receipts.exists()
    from inventory.purchasing import order_summary
    workflow=order_summary(order)
    if request.method == 'POST':
        valid = form.is_valid() and lines.is_valid()
        if valid and len(lines.forms) != len(order.items):
            form.add_error(None, 'Состав заказа изменился. Обновите страницу.')
            valid = False
        if valid:
            with transaction.atomic():
                current = PurchaseOrder.objects.select_for_update().get(pk=order.pk, user=request.user)
                if current.archived or form.cleaned_data['version'] != current.version:
                    form.add_error(None, 'Заказ изменён в другой вкладке. Обновите страницу перед сохранением.')
                else:
                    if current.stock_receipts.exists() or current.payments.exists():
                        changed=any(line['quantity'] != item['quantity'] or line['cost'] != Decimal(str(item['cost'])) for item,line in zip(current.items,lines.cleaned_data))
                        if changed or form.cleaned_data['status']=='cancelled':
                            messages.error(request,'После приёмки нельзя менять состав и цены заказа или отменять его. Реквизиты и документы можно дополнить.')
                            return redirect('procurement:purchase_detail',pk=pk)
                    if form.cleaned_data['status'] in ['paid','received'] and form.cleaned_data['status']!=current.status:
                        messages.error(request,'Оплата и приёмка рассчитываются по операциям. Используйте соответствующие кнопки заказа.')
                        return redirect('procurement:purchase_detail',pk=pk)
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
                    if current.status!=before['status'] and current.status in ['draft','sent','confirmed','cancelled']:
                        current.delivery_stage={'draft':'draft','sent':'sent','confirmed':'waiting','cancelled':'cancelled'}[current.status]
                    current.items = updated_items
                    current.version += 1
                    current.save(update_fields=fields + ['items', 'version', 'updated_at','delivery_stage'])
                    for field, kind in [('invoice_file', 'invoice'), ('waybill_file', 'waybill')]:
                        file = form.cleaned_data.get(field)
                        if file:
                            PurchaseDocument.objects.create(order=current, kind=kind, name=PurePath(file.name).name[:255], data=file.read())
                    Activity.objects.create(user=request.user, action='purchase_updated', details={'purchase': current.pk, 'before': before, 'version': current.version})
                    messages.success(request, 'Изменения закупочного заказа сохранены.')
                    return redirect('procurement:purchase_detail', pk=current.pk)
    events=list(Activity.objects.filter(user=request.user,details__purchase=pk).order_by('-pk')[:50])
    labels={'purchase_created':'Заказ создан','purchase_updated':'Реквизиты или состав изменены','purchase_planned':'План поставки изменён','purchase_stock_received':'Приёмка на склад','purchase_payment':'Денежная операция','purchase_delete':'Заказ удалён','purchase_restore':'Заказ восстановлен','purchase_remove':'Позиция убрана из заказа','purchase_listing':'Состояние продажи изменено','purchase_supplier_return':'Товар возвращён поставщику'}
    for event in events:
        event.display_action=labels.get(event.action,'Изменение закупки')
        event.reason=event.details.get('reason','')
    return render(request, 'procurement/purchase_detail.html', context(request, purchase=order, terms=supplier_terms(order.supplier, order.total), form=form, lines=lines,
        purchase_events=events, workflow=workflow, has_receipts=has_receipts, receipt_complete=has_receipts and sum(order.stock_receipts.filter(status='posted',kind='receipt').values_list('quantity',flat=True)) >= sum(i['quantity'] for i in order.items), stock_receipts=order.stock_receipts.select_related('item').order_by('pk'), item_forms=list(zip(order.items, lines.forms)), documents=order.documents.defer('data').all()), status=400 if request.method == 'POST' else 200)


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
        return HttpResponse('Подтвердите артикул, название и комплектность, затем бренд карточки.', status=400)
    offer.match_verified = True
    offer.save(update_fields=['match_verified'])
    Activity.objects.create(user=request.user, action='match_verified', details={'offer': offer.pk, 'method': 'manual_article_name_brand', 'source_url': offer.kaspi_url, 'article': offer.article, 'name': offer.name, 'brand': offer.brand})
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


@access()
def supplier_detail(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    return render(request, 'procurement/supplier.html', context(request, supplier=supplier, terms=supplier_terms(supplier)))


@access('sales', 'admin')
@require_POST
def refresh_shared_fitments(request):
    supplier = request_supplier(request)
    if supplier is None:
        return HttpResponse('Выберите поставщика.', status=400)
    preferences, _ = Preferences.objects.get_or_create(user=request.user)
    offers = best_candidates_query(Offer.objects.filter(supplier=supplier), preferences, timezone.localdate())
    result = fill_from_shared_base(supplier, request.user, offers=offers)
    messages.success(request, f"Применимость дополнена у {result['offers']} товаров: "
        f"{result['make_model_pairs']} пар марка/модель. "
        f"Расхождений, оставленных на проверку: {result['conflicts']}.")
    return redirect(reverse('procurement:dashboard') + f'?supplier={supplier.pk}&mode=best')
