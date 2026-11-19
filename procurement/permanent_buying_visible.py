"""Read permanent buying opportunities without reapplying later import exclusions.

New imports may append a qualifying snapshot, but no import, changing supplier
stock, or new Kaspi price can remove an already admitted record.
"""
from decimal import Decimal
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone

from .analysis import evaluate
from .buying_visible import filtered_offer_ids
from .models import (PermanentBuyingItem, Favorite, KaspiBuyingCandidate,
                     KaspiPreparedCard, RetailListing)


def visible_permanent_buying_rows(request, preferences, filters):
    rows = PermanentBuyingItem.objects.select_related('offer', 'offer__supplier')
    supplier = filters.get('supplier')
    if supplier:
        rows = rows.filter(offer__supplier=supplier)
    offer_scope = filtered_offer_ids(filters)
    if offer_scope is not None:
        rows = rows.filter(offer_id__in=offer_scope)
    if filters.get('brand'):
        rows = rows.filter(offer__brand__icontains=filters['brand'])
    if filters.get('category'):
        rows = rows.filter(offer__category=filters['category'])
    if filters.get('q'):
        q = filters['q'].strip()
        rows = rows.filter(Q(offer__article__icontains=q) |
                           Q(offer__brand__icontains=q) |
                           Q(offer__name__icontains=q) |
                           Q(title__icontains=q) |
                           Q(offer__applicability__icontains=q))
    if filters.get('max_cost') is not None:
        rows = rows.filter(cost_snapshot__lte=filters['max_cost'])
    if filters.get('min_profit') is not None:
        rows = rows.filter(profit_snapshot__gte=filters['min_profit'])
    if filters.get('min_markup') is not None:
        rows = rows.filter(roi_snapshot__gte=filters['min_markup'])
    if filters.get('favorites'):
        rows = rows.filter(offer_id__in=Favorite.objects.filter(
            user=request.user).values('offer_id'))
    if filters.get('availability'):
        from .analysis import stock_status
        allowed = [item.pk for item in rows.iterator(chunk_size=500)
                   if stock_status(item.offer.stock) == filters['availability']]
        rows = rows.filter(pk__in=allowed)

    # Immutable money snapshots make the approved rank independent of price
    # updates. Equal profit is ordered by ROI, then stable record ID.
    rows = rows.order_by('-profit_snapshot', '-roi_snapshot', 'pk')
    paginator = Paginator(rows, 40)
    page = paginator.get_page(request.GET.get('page'))
    items = list(page.object_list)
    offer_ids = [x.offer_id for x in items]
    candidate_map = {
        x.product_id: x for x in KaspiBuyingCandidate.objects.filter(
            product_id__in=[i.product_id for i in items if i.product_id]
        )
    }
    prepared_photos = dict(KaspiPreparedCard.objects.filter(
        offer_id__in=offer_ids).exclude(photo_url='').values_list('offer_id', 'photo_url'))
    favorite_ids = set(Favorite.objects.filter(
        user=request.user, offer_id__in=offer_ids).values_list('offer_id', flat=True))
    result = []
    for item in items:
        offer = item.offer
        row = evaluate(offer, preferences, today=timezone.localdate())
        candidate_obj = candidate_map.get(item.product_id) if item.product_id else None
        candidate = None
        if item.source == 'apify':
            cities = candidate_obj.city_offers if candidate_obj else []
            counts = []
            for city in cities or []:
                if isinstance(city, dict):
                    n = city.get('sellers_count')
                    if isinstance(n, int) and not isinstance(n, bool) and n >= 0:
                        counts.append(n)
                    elif isinstance(n, str) and n.isdecimal():
                        counts.append(int(n))
                    elif isinstance(city.get('sellers'), list):
                        counts.append(len(city['sellers']))
            candidate = {'id': item.product_id, 'title': item.title,
                         'url': item.kaspi_url,
                         'match_status': item.identity_status,
                         'seller_count': max(counts) if counts else None}
        photo = item.photo_url or offer.photo_url or prepared_photos.get(offer.pk, '')
        after_commission = (item.kaspi_price_snapshot *
                            (Decimal('1') - item.commission_snapshot / Decimal('100')))
        row.update({
            'offer': offer, 'candidate': candidate,
            'favorite': offer.pk in favorite_ids,
            'display_photo_url': photo, 'preliminary_photo': False,
            'display_cost': item.cost_snapshot,
            'kaspi_price': item.kaspi_price_snapshot,
            'profit': item.profit_snapshot,
            'markup': item.roi_snapshot,
            'commission_rate': item.commission_snapshot,
            'commission_amount': item.kaspi_price_snapshot - after_commission,
            'kaspi_after_commission': after_commission,
            'difference': after_commission - item.cost_snapshot,
            'expenses': item.expenses_snapshot,
            'historical': item.source == 'historical',
            'historical_reason': 'Постоянно сохранённая закупочная возможность',
            'best_status': ('Постоянный реестр · совпадение требует проверки'
                            if item.identity_status == 'candidate'
                            else 'Постоянный реестр · ранее сохранённый товар'),
            'display_supplier_date': offer.supplier_checked_on,
            'display_kaspi_date': None if item.source == 'apify' else offer.kaspi_price_checked_on,
            'processed_at': item.included_at,
            'kaspi_status': 'Сохранено бессрочно, проверяйте текущую цену и остаток',
            'kaspi_checked_on': None,
        })
        result.append(row)
    page.object_list = result
    request.catalog_page = page
    request.catalog_total = paginator.count
    request.apify_visible_total = rows.filter(source='apify').count()
    return result
