from .apify_quote_selection import INCOMPLETE_PRICE_SCOPE
from .catalogue_import import import_catalogue_fitments
from .shared_fitments import fill_from_shared_base
from .catalog_paging import catalog_page, best_candidates_query, stale_price_candidates_query, cumulative_profitable_query, MIN_BEST_ROI
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
from .suppliers import ACTIVE_SUPPLIER_NAMES

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
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.cache import never_cache
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.core.paginator import Paginator
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .analysis import evaluate, stock_status, MIN_BEST_PROFIT, PACKAGING_COST
from .permissions import workspace_role
from .forms import PreferencesForm, UploadForm, DraftForm, SelectionFilterForm, PurchaseConfirmationForm, PurchaseEditForm, PurchaseLineFormSet
from .importing import import_offers, import_demand, import_enrichment, import_competitors, import_matches, import_card_searches
from .apify_import import import_apify_kaspi
from .models import Membership, Supplier, Offer, Selection, Preferences, Draft, Activity, Favorite, VehicleFitment, PurchaseOrder, PurchaseDocument, PriceUpdate


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
            'best_url': reverse('procurement:dashboard') + '?supplier=&mode=best',
            'favorites_url': catalog_url + '&favorites=1',
            'upload_url': reverse('procurement:upload') + scope_query,
            'settings_url': reverse('procurement:settings') + scope_query,
            'basket_url': reverse('procurement:basket') + scope_query,
            'basket_count': Selection.objects.filter(user=request.user, offer__supplier__name__in=ACTIVE_SUPPLIER_NAMES).count(), **extra}


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


def latest_price_list_keys(batch_id):
    """Return exact article/brand pairs in a completed supplier upload.

    Cached by immutable batch ID: a new finished upload automatically changes
    the key, without rewriting old prices or loading the JSON on every request.
    """
    from django.core.cache import cache
    cache_key = f'procurement:last-price-keys:{batch_id}'
    keys = cache.get(cache_key)
    if keys is None:
        from .price_source import iter_price_rows
        keys = set()
        if PriceUpdate.objects.filter(pk=batch_id, status='done').exists():
            keys = {(row.get('brand'), row.get('article'))
                    for row in iter_price_rows(batch_id, fields=('brand', 'article', '_conflict'))
                    if isinstance(row, dict) and not row.get('_conflict')
                    and row.get('brand') and row.get('article')}
        cache.set(cache_key, keys, 3600)
    return keys


def preserved_best_scope(supplier):
    """Keep a supplier's last uploaded price and found deals visible until replacement.

    Historical records retain their original observation dates and are NOT
    relabelled as current or automatically authorised to purchase.
    Omega retains calculated candidates while their Kaspi prices/region need checking.
    """
    name = str(getattr(supplier, 'name', '') or '').strip().casefold()
    # An existing supplier's earlier profitable findings remain visible for
    # review. Unknown suppliers retain the conservative current-only scope.
    return 'all' if name in {'omega', 'zakazauto — костанай', 'фаетон'} else 'current'


def filtered_rows(request, preferences, filters, paginate=False):
    # Ranking uses scalar offer facts; detailed descriptions/JSON and seller lists
    # are not needed for every item in the supplier catalogue.
    query = Offer.objects.filter(supplier__name__in=ACTIVE_SUPPLIER_NAMES).select_related('supplier', 'kaspi_sellers', 'kaspi_card_search').defer('description', 'characteristics', 'supplier_url')
    mode = request.GET.get('mode', 'roi')
    if mode == 'best':
        # Firsthand offers are stored in JSON: load once with the row, not one
        # additional query per visible product.
        query = query.defer(None).defer('description', 'supplier_url').select_related('profitable_record')
    today = timezone.localdate()
    if mode == 'best':
        filters = {key: value for key, value in filters.items() if key not in ('kaspi_status', 'verified', 'sort')}
    if mode == 'best':
        query = cumulative_profitable_query(query, preferences, today)
    elif mode == 'stale_prices':
        query = stale_price_candidates_query(query, preferences, today)
    elif mode == 'kaspi_prices':
        query = query.filter(kaspi_price__gt=0)
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
    if paginate and mode == 'kaspi_prices' and not any(filters.get(key) for key in ('availability', 'demand')) and all(filters.get(key) is None for key in ('min_profit', 'min_markup')):
        ranked = query.order_by(F('kaspi_price_checked_on').desc(nulls_last=True), 'pk')
        request.catalog_page = Paginator(ranked.values_list('pk', flat=True), 40).get_page(request.GET.get('page'))
        request.catalog_total = request.catalog_page.paginator.count
        ids = list(request.catalog_page.object_list)
        by_id = {offer.pk: offer for offer in query.filter(pk__in=ids).order_by()}
        query = [by_id[pk] for pk in ids]
    if paginate and mode in ('roi', 'reviewed') and not any(filters.get(key) for key in ('availability', 'demand')) and all(filters.get(key) is None for key in ('min_profit', 'min_markup')):
        request.catalog_page, request.catalog_status_counts = catalog_page(query, preferences, filters, mode, request.GET.get('page'), today)
        request.catalog_total = request.catalog_page.paginator.count
        query = request.catalog_page.object_list
    rows = []
    latest_batches = {}
    latest_batch_members = {}
    if mode == 'best':
        for supplier_id, batch_id, batch_date in PriceUpdate.objects.filter(status='done').order_by('-pk').values_list('supplier_id', 'pk', 'checked_on'):
            latest_batches.setdefault(supplier_id, (batch_id, batch_date))
    for offer in query:
        if mode == 'best':
            latest_batch = latest_batches.get(offer.supplier_id)
            # The buying list is limited to the last completed supplier price list.
            if latest_batch:
                if offer.supplier_id not in latest_batch_members:
                    latest_batch_members[offer.supplier_id] = latest_price_list_keys(latest_batch[0])
                if (offer.supplier_checked_on != latest_batch[1] or
                        (offer.brand, offer.article) not in latest_batch_members[offer.supplier_id]):
                    continue
        row = {'offer': offer, 'favorite': offer.pk in favorites, **evaluate(offer, preferences, today=today)}
        row.update(kaspi_status(offer, preferences, today))
        row['kaspi_price'] = offer.kaspi_price
        if row['kaspi_status_code'] in ('empty', 'missing') and mode not in ('best', 'kaspi_prices'):
            row.update({key: None for key in ('kaspi_price', 'difference', 'markup', 'profit', 'margin', 'commission_amount', 'kaspi_after_commission')})
        if mode == 'kaspi_prices':
            for field in ('difference', 'markup', 'profit', 'margin', 'commission_amount', 'kaspi_after_commission'):
                row[field] = None
        if mode == 'best':
            record = getattr(offer, 'profitable_record', None)
            is_omega = offer.supplier.name == 'Omega'
            threshold = max(MIN_BEST_PROFIT, preferences.best_min_profit)
            is_bearing = any(x in (offer.name + ' ' + offer.category).casefold()
                             for x in ('подшипник', 'bearing'))
            firsthand = (offer.characteristics or {}).get('firsthand_omega_20261117') if is_omega else None
            row['firsthand'] = firsthand
            row['firsthand_images'] = (firsthand or {}).get('images', [])
            if is_omega:
                # Genuine three-city offers are preliminary until variant and
                # whole-country coverage are reviewed. Do not mark confirmed.
                observed_match = bool(firsthand and firsthand.get('identity_status') in ('candidate', 'conflict'))
                omega_margin_ok = bool(
                    (offer.match_verified or observed_match)
                    and bool(offer.kaspi_url) and offer.cost >= Decimal('2000')
                    and row['stock_status'] == 'in_stock'
                    and row['profit'] is not None
                    and row['profit'] >= offer.cost * Decimal('0.50')
                )
                is_current = False  # not yet all-Kazakhstan/variant confirmed
                row['omega_review'] = omega_margin_ok
            else:
                is_current = bool(
                    offer.match_verified and not is_bearing and offer.cost > 0
                    and row['stock_status'] == 'in_stock'
                    and row['supplier_fresh'] and row['price_fresh']
                    and row['profit'] is not None and row['profit'] >= threshold
                    and row['profit'] >= offer.cost * MIN_BEST_ROI / Decimal('100')
                )
                row['omega_review'] = False
            if not is_current and not row['omega_review']:
                continue
            row['historical'] = False
            # A newly imported Firsthand quote is a fresh financial lead, not
            # a confirmed product match. Keep that distinction separate from
            # the tab's "new" and "latest data" classifications.
            row['firsthand_recent'] = bool(
                row['omega_review'] and firsthand
                and row['price_fresh'] and row['supplier_fresh']
            )
            latest = latest_batches.get(offer.supplier_id)
            row['is_new'] = bool(
                (is_current and latest and (
                    (record and not record.initial_import and record.first_batch_id == latest[0]) or
                    (record is None and offer.supplier_checked_on == latest[1])
                )) or row['firsthand_recent']
            )
            row['processed_at'] = record.processed_at if record else None
            row['display_cost'] = offer.cost
            row['display_supplier_date'] = offer.supplier_checked_on
            row['display_kaspi_date'] = offer.kaspi_price_checked_on
            if not is_current:
                # Use first documented profit only for an actual historical
                # record; a newly promising stale quote retains its true dates.
                if record and not row['omega_review']:
                    row['display_cost'] = record.source_cost
                    row['display_supplier_date'] = record.supplier_date
                    row['display_kaspi_date'] = record.kaspi_date
                    row['kaspi_price'] = record.source_kaspi_price
                    row['profit'] = record.source_profit
                    row['markup'] = record.source_roi
                    row['commission_rate'] = record.commission
                    row['expenses'] = record.expenses
                    row['commission_amount'] = record.source_kaspi_price * record.commission / Decimal('100')
                    row['kaspi_after_commission'] = record.source_kaspi_price - row['commission_amount']
                    row['difference'] = row['kaspi_after_commission'] - record.source_cost
                if row['omega_review']:
                    if firsthand:
                        row['historical_reason'] = (
                            'Спорное соответствие: ' + firsthand.get('identity_note', '')
                            if firsthand.get('identity_status') == 'conflict' else
                            'Firsthand: сохранены цены и продавцы по городам: '
                            + ', '.join(dict.fromkeys(
                                city.get('city', '') for city in firsthand.get('cities', [])
                                if city.get('city')
                            ))
                            + '; исполнение и комплектность пока не подтверждены'
                        )
                        row['historical_reason'] += '; ' + (
                            firsthand.get('price_scope_note') or INCOMPLETE_PRICE_SCOPE
                        )
                    else:
                        row['historical_reason'] = (
                            'Требуется повторная проверка цены Kaspi по Казахстану'
                            if not row['price_fresh'] else
                            'Требуется подтвердить цены и доступность по городам Казахстана'
                        )
                    if not row['supplier_fresh']:
                        row['historical_reason'] += '; обновить прайс и остаток Омеги'
                else:
                    row['historical_reason'] = (
                        'Нет подтверждённого остатка по последнему прайсу'
                        if row['stock_status'] != 'in_stock'
                        else 'Не подтверждено совпадение с Kaspi'
                        if not offer.match_verified else
                        'Цена Kaspi устарела или дата не подтверждена'
                        if not row['price_fresh'] else
                        'Прайс поставщика устарел'
                        if not row['supplier_fresh'] else
                        'Расчётная доходность ниже утверждённого порога'
                    )
            if row['profit'] is not None and row['display_cost'] > 0:
                row['markup'] = row['profit'] / row['display_cost'] * Decimal('100')
            row['best_status'] = (
                'Спорное соответствие Firsthand — проверить вручную'
                if firsthand and firsthand.get('identity_status') == 'conflict' else
                'Предварительно выгодный — Firsthand'
                if row['omega_review'] and firsthand else
                'Предварительно выгодный — проверить Kaspi по Казахстану'
                if row['omega_review'] else
                'Ранее выгодный' if row['historical'] else
                'Новый' if row['is_new'] else 'Выгодный по сохранённым данным'
            )
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
        scope = request.GET.get('best_scope') or preserved_best_scope(filters.get('supplier'))
        if scope == 'new':
            rows = [r for r in rows if r['is_new']]
        elif scope == 'history':
            rows = [r for r in rows if r['historical'] and not r.get('firsthand_recent')]
        elif scope == 'current':
            # Include fresh three-city Firsthand financial candidates, while
            # their purchase action remains blocked by omega_review.
            rows = [r for r in rows if not r['historical'] or r.get('firsthand_recent')]
        rows.sort(key=lambda r: (-(r['markup'] or Decimal('0')),
                                -(r['profit'] or Decimal('0')), r['offer'].pk))
    elif mode == 'kaspi_prices':
        rows.sort(key=lambda r: (r['offer'].kaspi_price_checked_on is None,
                                 -(r['offer'].kaspi_price_checked_on.toordinal() if r['offer'].kaspi_price_checked_on else 0),
                                 r['offer'].pk))
    elif mode == 'stale_prices':
        # An old Kaspi price is only a review lead: require actual stock and
        # preserve historical price/date as-is, never claim a confirmed deal.
        rows = [r for r in rows if r['stock_status'] == 'in_stock' and r['supplier_fresh']
                and r['profit'] is not None
                and r['profit'] >= max(MIN_BEST_PROFIT, preferences.best_min_profit)]
        rows.sort(key=lambda r: (-(r['profit'] or Decimal(0)), r['offer'].pk))
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
@never_cache
def dashboard(request):
    # Small read-only endpoint for live Apify status; skips expensive product queries.
    if request.GET.get('apify_status') == '1':
        from .apify_batch_intake import upload_status
        return JsonResponse(upload_status(request.GET.get('apify_source', '').strip()[:255]))
    if request.GET.get('mode') != 'best' and 'supplier' not in request.GET and request.session.get('procurement_supplier'):
        params = request.GET.copy()
        params['supplier'] = request.session['procurement_supplier']
        return redirect(reverse('procurement:dashboard') + '?' + params.urlencode())
    started = perf_counter()
    preferences, _ = Preferences.objects.get_or_create(user=request.user)
    filter_data = request.GET
    if request.GET.get('mode') in ('best', 'stale_prices', 'kaspi_prices'):
        filter_data = request.GET.copy()
        for key in ('kaspi_status', 'verified', 'sort'):
            filter_data.pop(key, None)
    form = SelectionFilterForm(
        filter_data,
        include_historical_suppliers=request.GET.get('mode') == 'best',
        minimal_buying=request.GET.get('mode') == 'best',
    )
    valid = form.is_valid()
    form_finished = perf_counter()
    if valid:
        supplier = form.cleaned_data.get('supplier')
        request.session['procurement_supplier'] = supplier.pk if supplier else None
    else:
        supplier = form.cleaned_data.get('supplier')
    # The buying screen does not display global supplier inventory totals.
    # Avoid a needless COUNT(*) over the 56k-item catalogue on each visit.
    supplier_stats = None
    if supplier and request.GET.get('mode') != 'best':
        supplier_stats = {'loaded': Offer.objects.filter(supplier=supplier).count()}
    if valid and request.GET.get('mode') == 'best':
        from .permanent_buying_visible import visible_permanent_buying_rows
        rows = visible_permanent_buying_rows(request, preferences, form.cleaned_data)
    else:
        rows = filtered_rows(request, preferences, form.cleaned_data, paginate=request.GET.get('download') != 'csv') if valid else []
    rows_finished = perf_counter()
    best_empty_reason = ''
    if request.GET.get('mode') == 'best' and not rows:
        best_empty_reason = 'По выбранным фильтрам выгодных товаров нет.'
    counts = getattr(request, 'catalog_status_counts', {})
    form.fields['kaspi_status'].choices = [(key, f'{label} ({counts.get(key, 0)})') for key, label in form.fields['kaspi_status'].choices]
    preliminary = False
    displayed_rows = rows
    if request.GET.get('download') == 'apify' and request.GET.get('mode') == 'best' and valid:
        from .kaspi_search_queries import monitoring_search_queries
        from .apify_cities import APIFY_CITIES
        queries = list(dict.fromkeys(q for row in rows
            if row['offer'].supplier.name == 'Phaeton DC'
            for q in monitoring_search_queries(row['offer'].brand, row['offer'].article, row['offer'].name)))
        from .selection_exports import known_card_urls
        known_urls = known_card_urls([{'brand': row['offer'].brand, 'article': row['offer'].article}
            for row in rows if row['offer'].supplier.name == 'Phaeton DC'])
        response = JsonResponse(dict(searchQueries=queries, startUrls=known_urls, cities=list(APIFY_CITIES),
            sortOrder='relevance', maxItems=0, maxItemsPerQuery=20,
            includeOffers=True, maxOffersPerProduct=0, includeReviews=False),
            json_dumps_params={'ensure_ascii': False, 'indent': 2})
        response['Content-Disposition'] = 'attachment; filename="phaeton_price_recheck.json"'
        return response
    if request.GET.get('download') == 'csv' and valid:
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="procurement-selection.csv"'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        def exported_number(value):
            return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if value is not None else None
        writer.writerow(['Артикул', 'Бренд', 'Наименование', 'Поставщик', 'Закупка, ₸', 'Kaspi, ₸', 'Разница после комиссии, ₸', 'Доходность после комиссии, %', 'Прибыль после комиссии и прочих расходов, ₸', 'Спрос', 'Продажи / 30 дней', 'Найдено отзывов / 90 дней', 'Дата цены', 'Дата проверки спроса', 'Ссылка Kaspi', 'Комиссия без НДС, %', 'Комиссия, ₸', 'Kaspi после комиссии, ₸', 'Прочие расходы, ₸', 'Статус подбора', 'Дата прайса', 'Дата обработки', 'Дата Kaspi'])
        for row in displayed_rows:
            offer = row['offer']
            if request.GET.get('mode') == 'no_sellers':
                row = {**row, **{key: None for key in ('difference', 'markup', 'profit', 'commission_amount', 'kaspi_after_commission')}}
            writer.writerow([safe_csv(x) for x in [offer.article, offer.brand, offer.name, offer.supplier.name,
                exported_number(row.get('display_cost', offer.cost)), exported_number(row['kaspi_price']) if request.GET.get('mode') != 'no_sellers' else None, exported_number(row['difference']),
                exported_number(row['markup']), exported_number(row['profit']), row['signal'], offer.sales_30,
                offer.recent_reviews, offer.checked_on, offer.evidence_date, offer.kaspi_url,
                exported_number(row['commission_rate']), exported_number(row['commission_amount']),
                exported_number(row['kaspi_after_commission']), exported_number(row['expenses']), row.get('best_status', 'Сохранённая цена Kaspi — проверка требуется' if request.GET.get('mode') == 'kaspi_prices' else 'Устаревшая цена Kaspi' if request.GET.get('mode') == 'stale_prices' else 'Общий каталог'), row.get('display_supplier_date', offer.supplier_checked_on), row.get('processed_at'), row.get('display_kaspi_date', offer.kaspi_price_checked_on)]])
        return response
    page = getattr(request, 'catalog_page', None) or Paginator(displayed_rows, 40).get_page(request.GET.get('page'))
    prefetch_related_objects([row['offer'] for row in page.object_list],
        Prefetch('fitments', queryset=VehicleFitment.objects.only('id', 'offer_id', 'make', 'model', 'verified')[:4], to_attr='compact_fitments'),
        'kaspi_sellers')
    selected_ids = set(Selection.objects.filter(
        user=request.user, offer_id__in=[row['offer'].pk for row in page.object_list]
    ).values_list('offer_id', flat=True))
    for row in page.object_list:
        row['in_basket'] = row['offer'].pk in selected_ids
    if request.GET.get('mode') == 'best':
        from .compact_applicability import populate_compact_applicability
        populate_compact_applicability(page.object_list)

    apify_in_best = 0
    apify_sources = []
    apify_selected_source = request.GET.get('apify_source', '').strip()[:255]
    apify_summary_source = ''
    apify_summary = None
    if request.GET.get('mode') == 'best':
        from .permanent_buying_visible import apify_source_options, apify_source_summary
        apify_sources = apify_source_options()
        available = {entry['source_filename'] for entry in apify_sources}
        if apify_selected_source and apify_selected_source not in available:
            apify_selected_source = ''
        apify_summary_source = apify_selected_source or (
            apify_sources[0]['source_filename'] if apify_sources else '')
        apify_summary = apify_source_summary(apify_summary_source, supplier)
    # The displayed row count already comes from the paginated buying list.
    # A second count by Apify source was misleading and unnecessary.
    response = render(request, 'procurement/dashboard.html', context(request, apify_in_best=apify_in_best, apify_sources=apify_sources, apify_selected_source=apify_selected_source, apify_summary_source=apify_summary_source, apify_summary=apify_summary, page=page, total=getattr(request, 'catalog_total', len(rows)), preferences=preferences,
                  commission_rate=preferences.commission.normalize() if preferences.commission is not None else None,
                  effective_expenses=max(PACKAGING_COST, preferences.expenses) if preferences.expenses is not None else None,
                  filter_form=form, buying_order_choices=getattr(request, 'buying_order_choices', []), preliminary=preliminary, preliminary_total=len(displayed_rows) if preliminary else 0, best_empty_reason=best_empty_reason, supplier_stats=supplier_stats, catalogue_total=(supplier_stats['loaded'] if supplier_stats else Offer.objects.count()) if request.GET.get('mode') != 'best' else None,
                  reset_url=reverse('procurement:dashboard') + (f'?supplier={supplier.pk}' if supplier else '?supplier=') + ('&mode=' + request.GET['mode'] if request.GET.get('mode') in ('best', 'stale_prices', 'kaspi_prices', 'difference', 'no_sellers', 'reviewed') else ''),
                  page_numbers=page.paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1), basic_filters=[form[name] for name in (('brand', 'category', 'kaspi_seller_count') if request.GET.get('mode') == 'best' else ('brand', 'category', 'vehicle_country', 'vehicle_make', 'vehicle_model', 'vehicle_generation', 'vehicle_year', 'kaspi_seller_count'))],
                  advanced_filters=[form[name] for name in (('fitment', 'availability', 'demand', 'max_cost', 'favorites') if request.GET.get('mode') == 'kaspi_prices' else ('sort', 'fitment', 'availability', 'demand', 'max_cost', 'min_profit', 'min_markup', 'verified', 'favorites') if request.GET.get('mode') != 'best' else ('availability', 'max_cost', 'min_profit', 'min_markup', 'favorites'))],
                  kaspi_saved_total=Offer.objects.filter(kaspi_price__gt=0, **({'supplier': supplier} if supplier else {})).count() if request.GET.get('mode') != 'best' else None,
                  mode=request.GET.get('mode', 'roi'), best_scope=request.GET.get('best_scope') or preserved_best_scope(supplier), favorites=bool(form.cleaned_data.get('favorites')) if valid else False), status=200 if valid else 400)
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
    alternatives = list(Offer.objects.filter(brand__iexact=offer.brand, article__iexact=offer.article,
        supplier__name__in=ACTIVE_SUPPLIER_NAMES).select_related('supplier').order_by('cost', 'pk'))
    available_costs = [item.cost for item in alternatives if stock_status(item.stock) == 'in_stock']
    minimum_cost = min(available_costs) if available_costs else None
    for item in alternatives:
        item.minimum_available_cost = item.cost == minimum_cost and stock_status(item.stock) == 'in_stock'
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
def product_processing(request):
    """Read-only manual-enrichment dashboard; never processes tasks in a request."""
    from catalog.models import SupplierPhotoTask
    from .models import KaspiReviewTask
    from django.db.models import Count
    statuses = list(
        SupplierPhotoTask.objects.values('status').annotate(total=Count('pk'))
        .order_by('status')
    )
    labels = {
        'done': 'Проверка завершена (фото добавлено не обязательно)',
        'pending': 'Ожидает проверки',
        'prepared': 'Подготовлено к проверке',
        'needs_source': 'Не найден надёжный источник',
        'skipped': 'Пропущено',
        'blocked': 'Источник недоступен',
        'failed': 'Ошибка проверки',
    }
    for item in statuses:
        item['label'] = labels.get(item['status'], 'На дополнительной проверке')
    queue = KaspiReviewTask.objects.all()
    supplier = request_supplier(request)
    if supplier:
        queue = queue.filter(offer__supplier=supplier)
    kaspi_statuses = queue.aggregate(
        pending=Count('pk', filter=Q(status='pending')),
        checking=Count('pk', filter=Q(status='checking')),
        blocked=Count('pk', filter=Q(status='blocked')),
    )
    return render(request, 'procurement/product_processing.html',
                  context(request, task_statuses=statuses, kaspi_statuses=kaspi_statuses,
                          photo_total=sum(item['total'] for item in statuses)))


@access('sales', 'admin')
def kaspi_data(request):
    """Kaspi-only import view; the legacy importer remains at its old URL."""
    return upload(request, kaspi_only=True)


@access('sales', 'admin')
def upload(request, kaspi_only=False):
    supplier = request_supplier(request)
    form = UploadForm(request.POST or None, request.FILES or None,
                      initial={'supplier': supplier.name if supplier else ''})
    # Deprecated all-supplier imports must not refill the retired buying data.
    form.fields['kind'].choices = [
        item for item in form.fields['kind'].choices if item[0] != 'apify_kaspi_all'
    ]
    form.fields['supplier'].initial = 'Omega'
    if kaspi_only:
        allowed_kinds = {'demand', 'competitors', 'matches', 'card_searches', 'apify_kaspi', 'apify_kaspi_all'}
        form.fields['kind'].choices = [
            item for item in form.fields['kind'].choices if item[0] in allowed_kinds
        ]
    if request.method == 'POST' and form.is_valid():
        if form.cleaned_data['supplier'].strip() not in ACTIVE_SUPPLIER_NAMES:
            form.add_error('supplier', 'Выберите Omega, Phaeton DC или ZakazAuto.')
        elif form.cleaned_data['checked_on'] > timezone.localdate():
            form.add_error('checked_on', 'Дата не может быть в будущем.')
        else:
            try:
                handler = {'demand': import_demand, 'catalog': import_offers, 'enrichment': import_enrichment, 'catalogue_fitments': import_catalogue_fitments, 'competitors': import_competitors, 'apify_kaspi': import_apify_kaspi, 'apify_kaspi_all': lambda file, supplier, date, user: import_apify_kaspi(file, 'Все поставщики', date, user), 'matches': import_matches, 'card_searches': import_card_searches}[form.cleaned_data['kind']]
                count = handler(form.cleaned_data['file'], form.cleaned_data['supplier'], form.cleaned_data['checked_on'], request.user)
            except Exception as error:
                # Import transaction rolls back completely; never expose file contents or credentials.
                form.add_error(None, str(error) if isinstance(error, ValueError) else 'Файл не прочитан. Проверьте формат и структуру.')
            else:
                messages.success(request, f'Загружено товаров: {count}.')
                if form.cleaned_data['kind'] == 'apify_kaspi_all':
                    return redirect('procurement:dashboard')
                imported_supplier = Supplier.objects.get(name=form.cleaned_data['supplier'].strip())
                request.session['procurement_supplier'] = imported_supplier.pk
                return redirect(reverse('procurement:dashboard') + f'?supplier={imported_supplier.pk}')
    return render(request, 'procurement/form.html', context(request, form=form, title=('Цены и спрос Kaspi' if kaspi_only else 'Загрузка данных'), help='Каталожная применимость: JSON с checked_on и offers (brand, article, source_brand, source_article, source_url, status=sourced, rows с make, model, years, engine, notes). Сохраняются источники и ограничения; уже заполненные записи не перезаписываются. Продавцы Kaspi: JSON с checked_on и offers (brand, article, source_url, city, complete, sellers). Цены самого предложения и спрос не изменяются. Применимость Omega: JSON с датой checked_on и массивом offers (brand, article, source_url, category, fitments). Импорт применимости не изменяет цены и спрос. Прайс: article, brand, name, cost; необязательно category, stock, min_order, kaspi_price, kaspi_url, supplier_url. Поддерживается исходный Excel Omega. Отчёт спроса: article, brand, sales_30, review_count, recent_reviews, last_review (YYYY-MM-DD). Неизвестные значения оставьте пустыми. Для sales_30 обязательны period_start и period_end: ровно 30 календарных дней включительно до указанной даты. Отчёт спроса должен быть приведён к этим колонкам; автоматическое чтение любого формата Kaspi пока не поддерживается. Импорт атомарный.'))


@access()
@require_POST
def select(request, pk):
    offer = get_object_or_404(Offer, pk=pk, supplier__name__in=ACTIVE_SUPPLIER_NAMES)
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
        return JsonResponse({'basket_count': Selection.objects.filter(user=request.user, offer__supplier__name__in=ACTIVE_SUPPLIER_NAMES).count(),
                             'message': 'Добавлено в список закупки.'})
    target = request.POST.get('next', '')
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        messages.success(request, 'Добавлено в список закупки.')
        return redirect(target)
    return redirect('procurement:basket')


@access()
@require_POST
def select_many(request):
    """Add explicitly checked offers to the user's draft, preserving quantities."""
    as_json = 'application/json' in request.headers.get('Accept', '')
    ids = request.POST.getlist('offer_ids')
    if not ids or len(ids) > 200 or any(not value.isdecimal() or len(value) > 18 for value in ids):
        message = 'Отметьте от 1 до 200 товаров.'
        return JsonResponse({'error': message}, status=400) if as_json else HttpResponse(message, status=400)
    ids = set(map(int, ids))
    with transaction.atomic():
        offers = list(Offer.objects.filter(pk__in=ids, supplier__name__in=ACTIVE_SUPPLIER_NAMES).order_by('pk'))
        if len(offers) != len(ids) or any(not 1 <= offer.min_order <= 100000 for offer in offers):
            message = 'Состав списка изменился. Обновите страницу и повторите выбор.'
            return JsonResponse({'error': message}, status=400) if as_json else HttpResponse(message, status=400)
        for offer in offers:
            Selection.objects.get_or_create(user=request.user, offer=offer,
                                            defaults={'quantity': offer.min_order})
    if as_json:
        return JsonResponse({'basket_count': Selection.objects.filter(user=request.user, offer__supplier__name__in=ACTIVE_SUPPLIER_NAMES).count(),
                             'selected_ids': sorted(ids), 'message': 'Добавлено в список закупки.'})
    target = request.POST.get('next', '')
    messages.success(request, 'Добавлено в список закупки.')
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(target)
    return redirect('procurement:basket')


@access()
@require_POST
def remove_selected(request):
    """Remove only explicitly checked items belonging to the signed-in purchaser."""
    raw_ids = request.POST.getlist('selection_ids')
    if not raw_ids or len(raw_ids) > 200 or any(not x.isdecimal() or len(x) > 18 for x in raw_ids):
        messages.error(request, 'Отметьте товары, которые нужно убрать (не более 200).')
        return redirect('procurement:basket')
    ids = set(map(int, raw_ids))
    with transaction.atomic():
        matching = Selection.objects.select_for_update().filter(user=request.user, pk__in=ids)
        if matching.count() != len(ids):
            messages.error(request, 'Список изменился. Обновите страницу и повторите выбор.')
            return redirect('procurement:basket')
        matching.delete()
    messages.success(request, f'Убрано позиций: {len(ids)}.')
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
    from .purchase_list_excel import offer_unit, PurchaseListExportForm, purchaser_name, organization_details
    rows = list(Selection.objects.filter(user=request.user, offer__supplier__name__in=ACTIVE_SUPPLIER_NAMES).select_related('offer__supplier').order_by('offer__supplier__name', 'pk'))
    from .purchase_readiness import blocked_permanent_offer_ids
    unverified_ids = blocked_permanent_offer_ids({row.offer_id for row in rows})
    for row in rows:
        row.needs_review = row.offer_id in unverified_ids
    grouped = {}
    for row in rows:
        row.subtotal = row.quantity * row.offer.cost
        row.unit = offer_unit(row.offer)
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
    return render(request, 'procurement/basket.html', context(request, selections=rows, unverified_count=len(unverified_ids), supplier_groups=list(grouped.values()),
        purchases=orders, budget=sum((r.subtotal for r in rows), Decimal(0)),
        export_form=PurchaseListExportForm(initial={'buyer': purchaser_name(request.user)}),
        buyer_details=organization_details()))


@access()
@require_GET
def basket_excel(request):
    from .purchase_list_excel import PurchaseListExportForm, export_purchase_list, purchaser_name
    form = PurchaseListExportForm(request.GET)
    if not form.is_valid():
        return HttpResponse('Укажите имя закупщика длиной до 200 символов.', status=400)
    rows = Selection.objects.filter(user=request.user, offer__supplier__name__in=ACTIVE_SUPPLIER_NAMES).select_related('offer__supplier').order_by('offer__supplier__name', 'pk')
    buyer = form.cleaned_data['buyer'] or purchaser_name(request.user)
    output = export_purchase_list(rows, buyer)
    return FileResponse(output, as_attachment=True,
        filename=f'Spisok_zakupki_ZPT_{timezone.localdate():%Y-%m-%d}.xlsx',
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@access()
def purchase_confirm(request, supplier_id):
    supplier = get_object_or_404(Supplier, pk=supplier_id, name__in=ACTIVE_SUPPLIER_NAMES)
    def selection_rows(lock=False):
        query = Selection.objects.filter(user=request.user, offer__supplier=supplier).select_related('offer').order_by('pk')
        return list(query.select_for_update(of=('self',)) if lock else query)
    rows = selection_rows()
    from .purchase_readiness import blocked_permanent_offer_ids
    unverified_ids = blocked_permanent_offer_ids({row.offer_id for row in rows})
    form = PurchaseConfirmationForm(request.POST or None, initial={'request_key': uuid.uuid4(),
        'selection_token': signing.dumps(purchase_snapshot(rows), salt='procurement-purchase')})
    if request.method == 'POST' and unverified_ids:
        return HttpResponse(
            'В списке есть товары с неподтверждёнными ценой или исполнением. '
            'Проверьте их либо удалите из списка до создания заказа.',
            status=400,
        )
    if request.method == 'POST' and not unverified_ids and form.is_valid():
        existing = PurchaseOrder.objects.filter(user=request.user, request_key=form.cleaned_data['request_key']).first()
        if existing:
            return redirect('procurement:purchase_detail', pk=existing.pk)
        try:
            reviewed = signing.loads(form.cleaned_data['selection_token'], salt='procurement-purchase', max_age=3600)
        except signing.BadSignature:
            reviewed = None
        with transaction.atomic():
            rows = selection_rows(lock=True)
            # Revalidate at creation time as well as on the GET form.
            blocked_now = blocked_permanent_offer_ids({row.offer_id for row in rows})
            snapshot = purchase_snapshot(rows)
            if blocked_now:
                form.add_error(None, 'Некоторые позиции не прошли проверку цены и исполнения. Заказ не создан.')
            elif not rows or reviewed != snapshot:
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
        unverified_ids=unverified_ids, selections=rows, terms=supplier_terms(supplier, sum((row.quantity * row.offer.cost for row in rows), Decimal(0))), budget=sum((row.quantity * row.offer.cost for row in rows), Decimal(0))), status=400 if request.method == 'POST' else 200)


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
