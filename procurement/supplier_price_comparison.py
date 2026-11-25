"""Independent, read-only supplier price-list comparison.

Uploads on this screen never update operational Offers or Kaspi records.
"""
import re
import hashlib
import json
import os
import fcntl
from pathlib import Path
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db import connection
from django.core.cache import caches
from django.conf import settings
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .analysis import stock_status
from .importing import load_rows, money
from .models import Offer, Supplier, SupplierComparisonPriceList
from .views import access, context
from .price_source import iter_json_rows

SUPPLIER_LABELS = ('Phaeton', 'ZakazAuto', 'Omega')


def _supplier_key(name):
    value = (name or '').casefold()
    if 'phaeton' in value or 'фаетон' in value:
        return 'Phaeton'
    if 'zakazauto' in value or 'заказавто' in value:
        return 'ZakazAuto'
    if 'omega' in value or 'омега' in value or 'solid prof' in value:
        return 'Omega'
    return None


def _identity(value):
    return re.sub(r'[^a-z0-9а-яё]', '', (value or '').casefold().replace('ё', 'е'))


def _parse_price_file(file):
    """Validate a supplier's raw Excel/CSV and retain only source facts."""
    raw = load_rows(file, max_rows=150000)
    rows = []
    skipped = 0
    for _, record in raw:
        brand = str(record.get('brand') or '').strip()
        article = str(record.get('article') or '').strip()
        name = str(record.get('name') or '').strip()
        stock = str(record.get('stock') if record.get('stock') is not None else '').strip()
        if not brand or not article or not name or not record.get('cost'):
            skipped += 1
            continue
        try:
            cost = money(record['cost'])
        except (ValueError, ArithmeticError, TypeError):
            skipped += 1
            continue
        if cost is None or not cost.is_finite() or cost <= 0:
            skipped += 1
            continue
        rows.append({'brand': brand[:100], 'article': article[:120],
                     'name': name[:500], 'category': str(record.get('category') or '')[:200],
                     'cost': str(cost), 'stock': stock[:100]})
    if not rows:
        raise ValueError('В файле нет товаров с брендом, артикулом, названием и положительной ценой.')
    if all(not item['stock'] for item in rows):
        raise ValueError('В прайсе нет колонки с наличием или остатками. Нужен остаток для сравнения.')
    available = sum(stock_status(item['stock']) == 'in_stock' for item in rows)
    return rows, len(raw), available, skipped


def _snapshot(min_cost=Decimal('0'), *, warm_only=False):
    # Only immutable uploaded comparison facts are reused. The operational
    # Omega fallback must always read live Offers. No personal data or retail
    # basket enters this private instance-local cache.
    versions = list(SupplierComparisonPriceList.objects.order_by('supplier_code')
                    .values_list('supplier_code', 'uploaded_at'))
    if connection.in_atomic_block or {code for code, _ in versions} != set(SUPPLIER_LABELS):
        return None if warm_only else _build_snapshot(min_cost)
    fingerprint = json.dumps([(code, stamp.isoformat()) for code, stamp in versions])
    key = 'comparison-v1:' + hashlib.sha256((fingerprint + ':' + str(min_cost.normalize())).encode()).hexdigest()
    cache = caches['supplier_comparison']
    if warm_only and cache.has_key(key):
        return None
    result = cache.get(key)
    if result is not None:
        return None if warm_only else result
    # Serialize cold builds across the HTTP worker and existing consumer, so
    # two simultaneous clicks cannot duplicate the large aggregation in RAM.
    root = Path(settings.CACHES['supplier_comparison']['LOCATION'])
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with os.fdopen(os.open(root / 'build.lock', os.O_CREAT | os.O_RDWR, 0o600), 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = cache.get(key)
        if result is None:
            result = _build_snapshot(min_cost)
            cache.set(key, result, 3600)
        return None if warm_only else result


def _build_snapshot(min_cost=Decimal('0')):
    result = {code: {'rows': [], 'count': 0, 'source': None} for code in SUPPLIER_LABELS}
    uploads = {item.supplier_code: item for item in SupplierComparisonPriceList.objects.defer('rows')}
    for code in SUPPLIER_LABELS:
        batch = uploads.get(code)
        if batch:
            result[code]['rows'] = iter_json_rows(SupplierComparisonPriceList, batch.pk, chunk_size=512)
            result[code]['count'] = batch.available_count
            result[code]['source'] = 'upload'
    # Preserve the previously working Omega catalogue until its own comparison
    # pricelist is uploaded, without modifying it or claiming it is an uploaded file.
    if 'Omega' not in uploads:
        ids = [pk for pk, name in Supplier.objects.values_list('pk', 'name')
               if _supplier_key(name) == 'Omega']
        if ids:
            fallback = (
                {'brand': brand, 'article': article, 'name': name,
                 'category': category, 'cost': str(cost), 'stock': stock}
                for brand, article, name, category, cost, stock in (
                Offer.objects.filter(supplier_id__in=ids, cost__gt=0)
                .values_list('brand', 'article', 'name', 'category', 'cost', 'stock')
                .iterator(chunk_size=128)
                )
            )
            result['Omega']['rows'] = fallback
            result['Omega']['source'] = 'catalogue'

    grouped = {}
    counts = {code: 0 for code in SUPPLIER_LABELS}
    for code in SUPPLIER_LABELS:
        for offer in result[code]['rows']:
            if stock_status(offer['stock']) != 'in_stock':
                continue
            brand = offer['brand']
            article = offer['article']
            if not _identity(brand) or not _identity(article):
                continue
            try:
                cost = Decimal(str(offer['cost']))
            except (ValueError, ArithmeticError):
                continue
            if not cost.is_finite() or cost <= 0:
                continue
            counts[code] += 1
            group = grouped.setdefault((_identity(brand), _identity(article)), {
                'brand': brand, 'article': article, 'name': offer['name'],
                'category': offer.get('category') or '', 'prices': {}, 'stock': {}})
            if code not in group['prices'] or cost < group['prices'][code]:
                group['prices'][code] = cost
                group['stock'][code] = offer['stock']
    # Apply the minimum to the best actual buying price for each SKU,
    # not to individual supplier quotes: a higher quote must never be presented
    # as the cheapest merely because a lower quote was filtered out.
    if min_cost > 0:
        grouped = {key: group for key, group in grouped.items()
                   if min(group['prices'].values()) >= min_cost}
    shared = 0
    wins = {code: 0 for code in SUPPLIER_LABELS}
    pairs = [('Phaeton', 'ZakazAuto'), ('Phaeton', 'Omega'), ('ZakazAuto', 'Omega')]
    overlaps = {f'{left}_{right}': 0 for left, right in pairs}
    brands_by_supplier = {code: set() for code in SUPPLIER_LABELS}
    articles_by_supplier = {code: set() for code in SUPPLIER_LABELS}
    for group in grouped.values():
        for code in group['prices']:
            brands_by_supplier[code].add(_identity(group['brand']))
            articles_by_supplier[code].add(_identity(group['article']))
    brand_overlaps = {f'{left}_{right}': len(brands_by_supplier[left] & brands_by_supplier[right])
                      for left, right in pairs}
    article_overlaps = {f'{left}_{right}': len(articles_by_supplier[left] & articles_by_supplier[right])
                        for left, right in pairs}
    rows = []
    for group in grouped.values():
        prices = group['prices']
        for left, right in pairs:
            if left in prices and right in prices:
                overlaps[f'{left}_{right}'] += 1
        cheapest = min(prices.values())
        winners = [code for code, cost in prices.items() if cost == cheapest]
        if len(prices) >= 2:
            shared += 1
            for code in winners:
                wins[code] += 1
        higher = max(prices.values()) if len(prices) > 1 else None
        saving = higher - cheapest if higher is not None else None
        group.update(winners=winners, winner=winners[0] if len(winners) == 1 else 'Равная цена',
                     saving=saving, percent=saving / higher * 100 if saving is not None else None,
                     alternatives=len(prices))
        rows.append(group)
    rows.sort(key=lambda item: (item['brand'].casefold(), item['article'].casefold()))
    omega_only = sum(1 for group in rows if 'Omega' in group['prices'] and group['alternatives'] == 1)
    return {'rows': rows, 'counts': counts, 'wins': wins, 'shared': shared,
            'overlaps': overlaps, 'brand_overlaps': brand_overlaps,
            'article_overlaps': article_overlaps, 'omega_only': omega_only}


@access('admin', 'sales')
@require_http_methods(['GET', 'POST'])
def supplier_price_comparison(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'recalculate':
            messages.success(request, 'Сравнение пересчитано по сохранённым прайсам.')
            return redirect('procurement:supplier_price_comparison')
        if action != 'upload':
            messages.error(request, 'Неизвестное действие.')
            return redirect('procurement:supplier_price_comparison')
        supplier = request.POST.get('supplier')
        file = request.FILES.get('file')
        if supplier not in SUPPLIER_LABELS or not file:
            messages.error(request, 'Выберите прайс и правильного поставщика.')
            return redirect('procurement:supplier_price_comparison')
        if file.size > 50 * 1024 * 1024 or not file.name.lower().endswith(('.xlsx', '.csv')):
            messages.error(request, 'Поддерживаются XLSX и CSV до 50 МБ.')
            return redirect('procurement:supplier_price_comparison')
        try:
            checked_on = date.fromisoformat(request.POST.get('checked_on', ''))
            if checked_on > timezone.localdate():
                raise ValueError('Дата прайса не может быть в будущем.')
            rows, row_count, available, skipped = _parse_price_file(file)
            with transaction.atomic():
                previous = SupplierComparisonPriceList.objects.select_for_update().filter(
                    supplier_code=supplier).first()
                if previous and previous.checked_on > checked_on:
                    raise ValueError('Уже загружен более новый прайс этого поставщика.')
                SupplierComparisonPriceList.objects.update_or_create(
                    supplier_code=supplier,
                    defaults={'filename': file.name[:255], 'checked_on': checked_on,
                              'uploaded_by': request.user, 'row_count': row_count,
                              'available_count': available, 'skipped_count': skipped,
                              'rows': rows})
        except (ValueError, TypeError, OSError, KeyError, UnicodeError) as error:
            messages.error(request, 'Прайс не загружен: ' + str(error)[:250])
        else:
            messages.success(request, f'{supplier}: прайс загружен — {row_count} строк, '
                             f'{available} с наличием, пропущено некорректных: {skipped}. '
                             'Сравнение обновлено.')
        return redirect('procurement:supplier_price_comparison')

    # The threshold is a view filter, not a change to source prices.
    raw_min = request.GET.get('min_cost', '2000').strip()
    try:
        minimum = Decimal(raw_min)
        if not minimum.is_finite() or minimum < 0 or minimum > Decimal('999999999999') or minimum.as_tuple().exponent < -2:
            raise ValueError
    except (ValueError, ArithmeticError):
        minimum = Decimal('2000')
        messages.warning(request, 'Некорректный порог цены. Применено значение 2 000 ₸.')
    # Prices and stock are deliberately not stored in Django's shared cache.
    snapshot = _snapshot(minimum)
    uploads = {batch.supplier_code: batch for batch in SupplierComparisonPriceList.objects.all().only(
        'supplier_code', 'filename', 'checked_on', 'uploaded_at', 'row_count',
        'available_count', 'skipped_count')}
    cards = [{'code': code, 'batch': uploads.get(code),
              'fallback': code == 'Omega' and code not in uploads and snapshot['counts'][code] > 0,
              'count': snapshot['counts'][code]} for code in SUPPLIER_LABELS]
    search = (request.GET.get('q') or '').strip()[:100]
    supplier = (request.GET.get('supplier') or '').strip()
    scope = request.GET.get('shared', '1')
    shared_only = scope == '1'
    present = request.GET.get('present', '')
    rows = snapshot['rows']
    if search:
        needle = search.casefold()
        rows = [r for r in rows if needle in (r['brand'] + ' ' + r['article'] + ' ' + r['name']).casefold()]
    if supplier in SUPPLIER_LABELS:
        rows = [r for r in rows if supplier in r['winners'] and r['alternatives'] > 1]
    if present in SUPPLIER_LABELS:
        rows = [r for r in rows if present in r['prices']]
    if shared_only:
        rows = [r for r in rows if r['alternatives'] > 1]
    elif scope == 'single':
        rows = [r for r in rows if r['alternatives'] == 1]
    order = request.GET.get('sort', 'percent')
    if order == 'saving':
        rows = sorted(rows, key=lambda r: (r['saving'] is None, -(r['saving'] or 0)))
    elif order == 'percent':
        rows = sorted(rows, key=lambda r: (r['percent'] is None, -(r['percent'] or 0)))
    elif order == 'price':
        rows = sorted(rows, key=lambda r: min(r['prices'].values()))
    paginator = Paginator(rows, 50)
    page = paginator.get_page(request.GET.get('page'))
    page_links = list(paginator.get_elided_page_range(
        number=page.number, on_each_side=2, on_ends=1,
    ))
    middle_page = max(1, (paginator.num_pages + 1) // 2)
    preserved = request.GET.copy()
    preserved.pop('page', None)
    preserved['min_cost'] = str(minimum)
    page_query = preserved.urlencode()
    return render(request, 'procurement/supplier_price_comparison.html',
                  context(request, page=page, snapshot=snapshot, cards=cards,
                          today=timezone.localdate().isoformat(), q=search,
                          min_cost=minimum,
                          supplier_filter=supplier, present_filter=present, shared_only=shared_only,
                          scope=scope, sort=order, displayed_count=len(rows),
                          page_links=page_links, middle_page=middle_page,
                          page_query=page_query,
                          page_ellipsis=Paginator.ELLIPSIS))
