"""Read-only comparison of current supplier offers, without Kaspi or purchasing changes."""
import re
from decimal import Decimal
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from .analysis import stock_status
from .models import Offer, Supplier
from .views import access, context


SUPPLIER_LABELS = ('Phaeton', 'ZakazAuto', 'Omega')


def _supplier_key(name):
    value = (name or '').casefold()
    if 'phaeton' in value or 'фаетон' in value or 'фает' in value:
        return 'Phaeton'
    if 'zakazauto' in value or 'заказавто' in value:
        return 'ZakazAuto'
    if 'omega' in value or 'омега' in value or 'solid prof' in value:
        return 'Omega'
    return None


def _identity(value):
    return re.sub(r'[^a-z0-9а-яё]', '', (value or '').casefold().replace('ё', 'е'))


def _snapshot():
    """Bounded cache prevents repeated scanning of large catalogues on each page."""
    suppliers = [(s.pk, _supplier_key(s.name)) for s in Supplier.objects.only('id', 'name')]
    by_id = {pk: label for pk, label in suppliers if label}
    if not by_id:
        return {'rows': [], 'counts': {}, 'wins': {}, 'shared': 0}
    # Cache invalidates every five minutes; source prices are never mutated.
    rows_by_key = {}
    counts = {label: 0 for label in SUPPLIER_LABELS}
    q = Offer.objects.filter(supplier_id__in=by_id, cost__gt=0).values_list(
        'supplier_id', 'brand', 'article', 'name', 'category',
        'cost', 'stock', 'supplier_checked_on').iterator(chunk_size=2000)
    for supplier_id, brand, article, name, category, cost, stock, checked_on in q:
        label = by_id[supplier_id]
        if not _identity(brand) or not _identity(article):
            continue
        if stock_status(stock) != 'in_stock':
            continue
        counts[label] += 1
        key = (_identity(brand), _identity(article))
        group = rows_by_key.setdefault(key, {'brand': brand, 'article': article,
            'name': name, 'category': category, 'prices': {}, 'stock': {}, 'dates': {}})
        # Lowest positive price of each supplier for the normalized SKU.
        if label not in group['prices'] or cost < group['prices'][label]:
            group['prices'][label] = cost
            group['stock'][label] = stock
            group['dates'][label] = checked_on
    wins = {label: 0 for label in SUPPLIER_LABELS}
    shared = 0
    rows = []
    for group in rows_by_key.values():
        prices = group['prices']
        cheapest = min(prices.values())
        winners = [name for name, price in prices.items() if price == cheapest]
        if len(prices) >= 2:
            shared += 1
            if len(winners) == 1:
                wins[winners[0]] += 1
        next_price = min((price for price in prices.values() if price > cheapest), default=None)
        saving = (next_price - cheapest) if next_price is not None else None
        percent = ((saving / next_price) * Decimal('100')) if saving is not None else None
        group.update(winner=winners[0] if len(winners) == 1 else 'Равная цена',
                     saving=saving, percent=percent, alternatives=len(prices))
        rows.append(group)
    rows.sort(key=lambda x: (x['brand'].casefold(), x['article'].casefold()))
    return {'rows': rows, 'counts': counts, 'wins': wins, 'shared': shared}


@access('admin', 'sales')
def supplier_price_comparison(request):
    snapshot = cache.get('procurement:supplier-price-comparison:v1')
    if snapshot is None:
        snapshot = _snapshot()
        cache.set('procurement:supplier-price-comparison:v1', snapshot, 300)
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
                  context(request, page=page, snapshot=snapshot, q=search, supplier_filter=supplier,
                          shared_only=shared_only, sort=order, displayed_count=len(rows)))
