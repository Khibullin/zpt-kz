"""Read permanent buying opportunities without reapplying later import exclusions.

New imports may append a qualifying snapshot, but no import, changing supplier
stock, or new Kaspi price can remove an already admitted record.
"""
from decimal import Decimal
from django.core.paginator import Paginator
from django.db.models import Q, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from .analysis import evaluate
from .buying_visible import filtered_offer_ids
from .models import (PermanentBuyingItem, Favorite, KaspiBuyingCandidate,
                     KaspiPreparedCard, KaspiPriceAudit, RetailListing)


def visible_permanent_buying_rows(request, preferences, filters):
    rows = PermanentBuyingItem.objects.select_related('offer', 'offer__supplier')
    # Quick entry to the most recent imported-price comparisons. This is a
    # display filter only: old permanent records and their snapshots remain.
    if request.GET.get('rechecked') == '1':
        rows = rows.filter(offer_id__in=KaspiPriceAudit.objects.values('offer_id'))
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
    # Prefer the newest separate seller-price audit for visible ranking.
    # Archived profits remain unchanged in PermanentBuyingItem.
    latest = KaspiPriceAudit.objects.filter(
        offer_id=OuterRef('offer_id')).order_by('-observed_at', '-pk')
    rows = rows.annotate(
        _latest_profit=Subquery(latest.values('profit')[:1]),
        _latest_roi=Subquery(latest.values('roi')[:1]),
    ).order_by(Coalesce('_latest_profit', 'profit_snapshot').desc(),
               Coalesce('_latest_roi', 'roi_snapshot').desc(), 'pk')
    paginator = Paginator(rows, 40)
    page = paginator.get_page(request.GET.get('page'))
    items = list(page.object_list)
    offer_ids = [x.offer_id for x in items]
    candidate_map = {
        x.product_id: x for x in KaspiBuyingCandidate.objects.filter(
            product_id__in=[i.product_id for i in items if i.product_id]
        )
    }
    audits = {}
    for audit in KaspiPriceAudit.objects.filter(
            offer_id__in=offer_ids).order_by('offer_id', '-observed_at', '-pk'):
        audits.setdefault(audit.offer_id, audit)
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
        # A card-level minPrice without actual seller offers is NOT a verified
        # market minimum. This status never mutates the permanent record.
        seller_prices = []
        if candidate_obj and isinstance(candidate_obj.city_offers, list):
            for city in candidate_obj.city_offers:
                if not isinstance(city, dict):
                    continue
                for seller in (city.get('sellers') or []):
                    if isinstance(seller, dict):
                        try:
                            price = Decimal(str(seller.get('price')))
                        except (ArithmeticError, ValueError, TypeError):
                            continue
                        if price.is_finite() and price > 0:
                            seller_prices.append(price)
        has_seller_evidence = bool(seller_prices)
        seller_min = min(seller_prices) if seller_prices else None
        identity_confirmed = item.identity_status == 'verified'
        if item.roi_snapshot < Decimal('50'):
            price_status = 'Доходность ниже 50% — сохраняется в реестре'
        elif not has_seller_evidence:
            price_status = 'Цена Kaspi не подтверждена предложениями продавцов'
        elif seller_min != item.kaspi_price_snapshot:
            price_status = 'Цена расходится с ценами продавцов — нужна проверка'
        elif not identity_confirmed:
            price_status = 'Цена найдена в выгрузке — исполнение требует проверки'
        else:
            price_status = 'Цена и совпадение подтверждены по сохранённым данным'
        # Saved historical data is preserved, but cannot silently be treated
        # as a current confirmed purchase price.
        purchase_ready = bool(item.source == 'apify' and identity_confirmed and
                              has_seller_evidence and seller_min == item.kaspi_price_snapshot
                              and item.roi_snapshot >= Decimal('50'))
        photo = item.photo_url or offer.photo_url or prepared_photos.get(offer.pk, '')
        after_commission = (item.kaspi_price_snapshot *
                            (Decimal('1') - item.commission_snapshot / Decimal('100')))
        row.update({
            'offer': offer, 'candidate': candidate,
            'purchase_ready': purchase_ready,
            'price_evidence_status': price_status,
            'has_seller_evidence': has_seller_evidence,
            'seller_minimum': seller_min,
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
        audit = audits.get(offer.pk)
        if audit:
            # Current comparison is a distinct calculation, not a rewrite of
            # the permanently saved historical price or its economic proof.
            row['previous_kaspi_price'] = item.kaspi_price_snapshot
            row['previous_profit'] = item.profit_snapshot
            row['previous_roi'] = item.roi_snapshot
            row['kaspi_price'] = audit.min_price
            row['display_cost'] = audit.cost_snapshot
            row['profit'] = audit.profit
            row['markup'] = audit.roi
            row['expenses'] = audit.expenses_snapshot
            row['commission_rate'] = audit.commission_snapshot
            row['commission_amount'] = (audit.min_price *
                                        audit.commission_snapshot / Decimal('100'))
            row['kaspi_after_commission'] = (audit.min_price -
                                             row['commission_amount'])
            row['difference'] = (row['kaspi_after_commission'] -
                                 audit.cost_snapshot)
            row['price_evidence_status'] = (
                'Минимальная среди найденных карточек и продавцов · '
                'исполнение требует проверки')
            if audit.roi < Decimal('50'):
                row['price_evidence_status'] = (
                    'По новой минимальной цене доходность ниже 50%')
            row['best_status'] = (
                'Повторная проверка Apify · ' +
                str(len(audit.card_ids or [])) + ' подходящих карточек')
            row['purchase_ready'] = False
            row['historical'] = False
            row['candidate'] = {
                'title': audit.card_title,
                'url': audit.card_url,
                'match_status': 'candidate',
                'seller_count': None,
            }
            row['display_kaspi_date'] = None
            row['audit_file'] = audit.source_filename
            row['audit_observed_at'] = audit.observed_at
        result.append(row)
    page.object_list = result
    request.catalog_page = page
    request.catalog_total = paginator.count
    request.apify_visible_total = rows.filter(source='apify').count()
    return result
