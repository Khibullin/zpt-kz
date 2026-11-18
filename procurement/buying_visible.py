"""Existing profitable-buying screen: show every matched Apify card, plus saved deals.

A Kaspi card is a display row; several cards may point at one supplier Offer.
No source Offer, warehouse, price or verification flag is mutated.
"""
from decimal import Decimal
from django.core.paginator import Paginator
from django.db.models import Q
from .models import KaspiBuyingCandidate, Offer, ProfitableOfferRecord
from .analysis import evaluate, PACKAGING_COST
from .catalog_paging import cumulative_profitable_query


def visible_buying_rows(request, preferences, filters):
    from django.utils import timezone
    supplier = filters.get('supplier')
    qs = KaspiBuyingCandidate.objects.filter(
        match_status__in=['candidate', 'verified'], offer__isnull=False,
    )
    if supplier:
        qs = qs.filter(offer__supplier=supplier)
    if filters.get('brand'):
        qs = qs.filter(offer__brand__icontains=filters['brand'])
    if filters.get('category'):
        qs = qs.filter(offer__category=filters['category'])
    if filters.get('q'):
        q = filters['q'].strip()
        qs = qs.filter(Q(offer__article__icontains=q) | Q(offer__brand__icontains=q)
                       | Q(offer__name__icontains=q) | Q(title__icontains=q))
    if filters.get('max_cost') is not None:
        qs = qs.filter(offer__cost__lte=filters['max_cost'])

    # Keep the Kaspi-card identity as the row key, never DISTINCT(offer_id).
    candidates = list(qs.values(
        'id', 'offer_id', 'minimum_price', 'url', 'city_offers', 'match_status',
        'offer__cost', 'offer__supplier_id',
    ))
    candidate_offer_ids = {item['offer_id'] for item in candidates}
    commission = preferences.commission
    expenses = preferences.expenses
    extra_cost = max(PACKAGING_COST, expenses) if expenses is not None else None

    def calculation(price, cost):
        if price is None or cost is None or cost <= 0 or commission is None or extra_cost is None:
            return None, None
        profit = price * (Decimal('1') - commission / Decimal('100')) - cost - extra_cost
        return profit, profit * Decimal('100') / cost

    indexed = []
    for candidate in candidates:
        profit, roi = calculation(candidate['minimum_price'], candidate['offer__cost'])
        if filters.get('min_profit') is not None and (profit is None or profit < filters['min_profit']):
            continue
        if filters.get('min_markup') is not None and (roi is None or roi < filters['min_markup']):
            continue
        indexed.append(('apify', candidate, profit, roi))

    # Previously saved profitable offers must not disappear on Apify import.
    # Include currently qualified legacy offers without duplicating an Apify supplier row.
    old = Offer.objects.select_related('supplier', 'profitable_record').exclude(
        pk__in=candidate_offer_ids
    )
    if supplier:
        old = old.filter(supplier=supplier)
    if filters.get('brand'):
        old = old.filter(brand__icontains=filters['brand'])
    if filters.get('category'):
        old = old.filter(category=filters['category'])
    if filters.get('q'):
        q = filters['q'].strip()
        old = old.filter(Q(article__icontains=q) | Q(brand__icontains=q) | Q(name__icontains=q))
    if filters.get('max_cost') is not None:
        old = old.filter(cost__lte=filters['max_cost'])
    legacy = cumulative_profitable_query(old, preferences)
    for offer in legacy.iterator(chunk_size=500):
        record = getattr(offer, 'profitable_record', None)
        price = (record.source_kaspi_price if record else offer.kaspi_price)
        cost = (record.source_cost if record else offer.cost)
        profit, roi = calculation(price, cost)
        if record:
            profit, roi = record.source_profit, record.source_roi
        if filters.get('min_profit') is not None and (profit is None or profit < filters['min_profit']):
            continue
        if filters.get('min_markup') is not None and (roi is None or roi < filters['min_markup']):
            continue
        indexed.append(('legacy', (offer.pk, cost, price, record), profit, roi))

    # Unknown profit is last; all losses remain visible on the default screen.
    indexed.sort(key=lambda r: (r[3] is None, -(r[3] or Decimal('0')),
                                r[2] is None, -(r[2] or Decimal('0')),
                                r[1]['id'] if r[0] == 'apify' else r[1][0]))
    paginator = Paginator(indexed, 40)
    page = paginator.get_page(request.GET.get('page'))
    offer_ids = [entry[1]['offer_id'] if entry[0] == 'apify' else entry[1][0]
                 for entry in page.object_list]
    offers = {o.pk: o for o in Offer.objects.filter(pk__in=offer_ids).select_related(
        'supplier', 'kaspi_sellers', 'kaspi_card_search'
    )}
    rows = []
    today = timezone.localdate()
    for kind, payload, profit, roi in page.object_list:
        offer = offers.get(payload['offer_id'] if kind == 'apify' else payload[0])
        if offer is None:
            continue
        analysis = evaluate(offer, preferences, today=today)
        if kind == 'apify':
            price = payload['minimum_price']
            cities = payload['city_offers'] or []
            counts = []
            for city in cities:
                n = city.get('sellers_count') if isinstance(city, dict) else None
                if isinstance(n, int) and not isinstance(n, bool) and n >= 0:
                    counts.append(n)
                elif isinstance(n, str) and n.isdecimal():
                    counts.append(int(n))
            count = max(counts) if counts else None
            candidate = {'id': payload['id'], 'url': payload['url'],
                         'seller_count': count, 'match_status': payload['match_status']}
            cost = offer.cost
        else:
            candidate = None
            cost, price = payload[1], payload[2]
        analysis.update({
            'offer': offer, 'candidate': candidate,
            'display_cost': cost, 'kaspi_price': price,
            'profit': profit, 'markup': roi,
            'best_status': (
                'Совпадение ранее проверено' if candidate and candidate['match_status'] == 'verified'
                else 'Совпадение по бренду и артикулу — требует проверки'
                if candidate else 'Ранее найденный выгодный товар'
            ),
            'display_supplier_date': offer.supplier_checked_on,
            'display_kaspi_date': (None if candidate else offer.kaspi_price_checked_on),
            'processed_at': None,
            'historical': kind == 'legacy',
            'historical_reason': 'Сохранённый прежний расчёт' if kind == 'legacy' else '',
        })
        rows.append(analysis)
    page.object_list = rows
    request.catalog_page = page
    request.catalog_total = paginator.count
    request.apify_visible_total = sum(item[0] == 'apify' for item in indexed)
    return rows
