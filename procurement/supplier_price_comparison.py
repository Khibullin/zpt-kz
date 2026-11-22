"""Independent, read-only supplier price-list comparison.

Uploads on this screen never update operational Offers or Kaspi records.
"""
import re
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .analysis import stock_status
from .importing import load_rows, money
from .models import Offer, Supplier, SupplierComparisonPriceList
from .views import access, context

SUPPLIER_LABELS = ('Phaeton', 'ZakazAuto', 'Omega')
CACHE_KEY = 'procurement:supplier-price-comparison:v2'


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


def _snapshot():
    result = {code: {'rows': [], 'count': 0, 'source': None} for code in SUPPLIER_LABELS}
    uploads = {item.supplier_code: item for item in SupplierComparisonPriceList.objects.all()}
    for code in SUPPLIER_LABELS:
        batch = uploads.get(code)
        if batch:
            result[code]['rows'] = batch.rows
            result[code]['count'] = batch.available_count
            result[code]['source'] = 'upload'
    # Preserve the previously working Omega catalogue until its own comparison
    # pricelist is uploaded, without modifying it or claiming it is an uploaded file.
    if 'Omega' not in uploads:
        ids = [pk for pk, name in Supplier.objects.values_list('pk', 'name')
               if _supplier_key(name) == 'Omega']
        if ids:
            fallback = []
            for brand, article, name, category, cost, stock in (
                Offer.objects.filter(supplier_id__in=ids, cost__gt=0)
                .values_list('brand', 'article', 'name', 'category', 'cost', 'stock')
                .iterator(chunk_size=2000)
            ):
                fallback.append({'brand': brand, 'article': article, 'name': name,
                                 'category': category, 'cost': str(cost), 'stock': stock})
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
    shared = 0
    wins = {code: 0 for code in SUPPLIER_LABELS}
    rows = []
    for group in grouped.values():
        prices = group['prices']
        cheapest = min(prices.values())
        winners = [code for code, cost in prices.items() if cost == cheapest]
        if len(prices) >= 2:
            shared += 1
            if len(winners) == 1:
                wins[winners[0]] += 1
        higher = min((v for v in prices.values() if v > cheapest), default=None)
        saving = higher - cheapest if higher is not None else None
        group.update(winner=winners[0] if len(winners) == 1 else 'Равная цена',
                     saving=saving, percent=saving / higher * 100 if saving is not None else None,
                     alternatives=len(prices))
        rows.append(group)
    rows.sort(key=lambda item: (item['brand'].casefold(), item['article'].casefold()))
    return {'rows': rows, 'counts': counts, 'wins': wins, 'shared': shared}


@access('admin', 'sales')
@require_http_methods(['GET', 'POST'])
def supplier_price_comparison(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'recalculate':
            cache.delete(CACHE_KEY)
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
            cache.delete(CACHE_KEY)
            messages.success(request, f'{supplier}: прайс загружен — {row_count} строк, '
                             f'{available} с наличием, пропущено некорректных: {skipped}. '
                             'Сравнение обновлено.')
        return redirect('procurement:supplier_price_comparison')

    snapshot = cache.get(CACHE_KEY)
    if snapshot is None:
        snapshot = _snapshot()
        cache.set(CACHE_KEY, snapshot, 300)
    uploads = {batch.supplier_code: batch for batch in SupplierComparisonPriceList.objects.all().only(
        'supplier_code', 'filename', 'checked_on', 'uploaded_at', 'row_count',
        'available_count', 'skipped_count')}
    cards = [{'code': code, 'batch': uploads.get(code),
              'fallback': snapshot.get(code, {}).get('source') == 'catalogue',
              'count': snapshot['counts'][code]} for code in SUPPLIER_LABELS]
    search = (request.GET.get('q') or '').strip()[:100]
    supplier = (request.GET.get('supplier') or '').strip()
    shared_only = request.GET.get('shared') == '1'
    rows = snapshot['rows']
    if search:
        needle = search.casefold()
        rows = [r for r in rows if needle in (r['brand'] + ' ' + r['article'] + ' ' + r['name']).casefold()]
    if supplier in SUPPLIER_LABELS:
        rows = [r for r in rows if r['winner'] == supplier]
    if shared_only:
        rows = [r for r in rows if r['alternatives'] > 1]
    order = request.GET.get('sort')
    if order == 'saving':
        rows = sorted(rows, key=lambda r: (r['saving'] is None, -(r['saving'] or 0)))
    elif order == 'price':
        rows = sorted(rows, key=lambda r: min(r['prices'].values()))
    page = Paginator(rows, 50).get_page(request.GET.get('page'))
    return render(request, 'procurement/supplier_price_comparison.html',
                  context(request, page=page, snapshot=snapshot, cards=cards,
                          today=timezone.localdate().isoformat(), q=search,
                          supplier_filter=supplier, shared_only=shared_only,
                          sort=order, displayed_count=len(rows)))
