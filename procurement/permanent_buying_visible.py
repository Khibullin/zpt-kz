"""Read permanent buying opportunities without reapplying later import exclusions.

New imports may append a qualifying snapshot, but no import, changing supplier
stock, or new Kaspi price can remove an already admitted record.
"""
from decimal import Decimal
from django.core.paginator import Paginator
from django.db.models import Q, F, OuterRef, Subquery, Max, DecimalField
from django.db.models.functions import Coalesce
from .catalog_paging import (consumable_query, profitable_product_exclusions,
                             MIN_CONSUMABLE_COST, MIN_PROFITABLE_COST,
                             MIN_PROFITABLE_ROI, is_consumable)
from django.utils import timezone

from .analysis import evaluate
from .buying_visible import filtered_offer_ids
from .comparison_next_fifty import NEXT_FIFTY_OFFER_IDS
from .comparison_next_hundred import NEXT_HUNDRED_OFFER_IDS
from .comparison_fourth_hundred import FOURTH_HUNDRED_OFFER_IDS
from .comparison_fifth_hundred import FIFTH_HUNDRED_OFFER_IDS
from .comparison_sixth_hundred import SIXTH_HUNDRED_OFFER_IDS
from .comparison_seventh_hundred import SEVENTH_HUNDRED_OFFER_IDS
from .comparison_eighth_two_hundred import EIGHTH_TWO_HUNDRED_OFFER_IDS
from .comparison_final_remaining import FINAL_REMAINING_OFFER_IDS
from .models import (PermanentBuyingItem, Favorite, KaspiBuyingCandidate,
                     KaspiPreparedCard, KaspiPriceAudit, RetailListing)



def apify_source_options():
    """Only actual, recorded audit exports; never infer dates from filenames."""
    return list(KaspiPriceAudit.objects.values('source_filename')
                .annotate(last_seen=Max('observed_at'))
                .order_by('-last_seen', 'source_filename')[:30])


def apify_source_summary(source, supplier=None):
    """Count visible opportunities in an export, distinguishing first audit from updates.

    'First observation' does not claim the SKU was newly created in the catalog.
    """
    if not source:
        return None
    audits = KaspiPriceAudit.objects.filter(source_filename=source,
        offer_id__in=PermanentBuyingItem.objects.values('offer_id'))
    if supplier:
        audits = audits.filter(offer__supplier=supplier)
    previous = KaspiPriceAudit.objects.filter(
        offer_id=OuterRef('offer_id'), pk__lt=OuterRef('pk')
    ).order_by('-pk')
    audits = audits.annotate(_prev_price=Subquery(previous.values('min_price')[:1]))
    counts = {'first': 0, 'changed': 0, 'unchanged': 0, 'total': 0,
              'eligible': 0, 'excluded': 0}
    for price, prior, roi, cost, name, category in audits.values_list(
            'min_price', '_prev_price', 'roi', 'cost_snapshot',
            'offer__name', 'offer__category').iterator(chunk_size=1000):
        counts['total'] += 1
        counts['first' if prior is None else 'changed' if price != prior else 'unchanged'] += 1
        eligible = (roi is not None and roi >= MIN_PROFITABLE_ROI and
                    cost is not None and cost >= MIN_PROFITABLE_COST)
        counts['eligible' if eligible else 'excluded'] += 1
    return counts


def visible_permanent_buying_rows(request, preferences, filters):
    rows = PermanentBuyingItem.objects.select_related('offer', 'offer__supplier')
    # Published second batch is a view of the SAME buying register, not a
    # separate catalogue or a new permanent insertion.
    next_fifty = request.GET.get('batch') == 'next50'
    next_hundred = request.GET.get('batch') == 'next100'
    fourth_hundred = request.GET.get('batch') == 'next100b'
    fifth_hundred = request.GET.get('batch') == 'next100c'
    sixth_hundred = request.GET.get('batch') == 'next100d'
    seventh_hundred = request.GET.get('batch') == 'next100e'
    eighth_two_hundred = request.GET.get('batch') == 'next200'
    final_remaining = request.GET.get('batch') == 'final153'
    if next_fifty:
        rows = rows.filter(offer_id__in=NEXT_FIFTY_OFFER_IDS)
    elif next_hundred:
        rows = rows.filter(offer_id__in=NEXT_HUNDRED_OFFER_IDS)
    elif fourth_hundred:
        rows = rows.filter(offer_id__in=FOURTH_HUNDRED_OFFER_IDS)
    elif fifth_hundred:
        rows = rows.filter(offer_id__in=FIFTH_HUNDRED_OFFER_IDS)
    elif sixth_hundred:
        rows = rows.filter(offer_id__in=SIXTH_HUNDRED_OFFER_IDS)
    elif seventh_hundred:
        rows = rows.filter(offer_id__in=SEVENTH_HUNDRED_OFFER_IDS)
    elif eighth_two_hundred:
        rows = rows.filter(offer_id__in=EIGHTH_TWO_HUNDRED_OFFER_IDS)
    elif final_remaining:
        rows = rows.filter(offer_id__in=FINAL_REMAINING_OFFER_IDS)
    # Profitable buying opens directly on products with an Apify price recheck.
    # Historical register records remain intact and accessible to maintenance
    # via the explicit legacy query parameter, not the ordinary buying screen.
    if request.GET.get('rechecked') != '0':
        rows = rows.filter(offer_id__in=KaspiPriceAudit.objects.values('offer_id'))
    source = request.GET.get('apify_source', '').strip()[:255]
    if source:
        rows = rows.filter(offer_id__in=KaspiPriceAudit.objects.filter(
            source_filename=source).values('offer_id'))
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
        # Search each typed word across all product fields. Full strings such as
        # "RAMEDER 20A040152" span separate brand/article fields; treating
        # them as one substring incorrectly returned zero results.
        for token in filters['q'].split():
            rows = rows.filter(
                Q(offer__article__icontains=token) |
                Q(offer__brand__icontains=token) |
                Q(offer__name__icontains=token) |
                Q(title__icontains=token) |
                Q(offer__applicability__icontains=token)
            )
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

    # Profitability is a DISPLAY rule, not a destructive archival operation.
    # The source/finance snapshots stay frozen; filter on the exact latest
    # audited numbers the table will render, before pagination and COUNT.
    # A selected export must never borrow a more profitable audit from another
    # export or silently fall back to an unrelated historical price.
    # Historic records without a new audit remain accessible elsewhere.
    # Springs are permitted along with all other categories.
    rows = rows.exclude(profitable_product_exclusions('offer__'))
    # Immutable money snapshots make the approved rank independent of price
    # updates. Equal profit is ordered by ROI, then stable record ID.
    # Prefer the newest separate seller-price audit for visible ranking.
    # Archived profits remain unchanged in PermanentBuyingItem.
    latest = KaspiPriceAudit.objects.filter(offer_id=OuterRef('offer_id'))
    if source:
        latest = latest.filter(source_filename=source)
    latest = latest.order_by('-observed_at', '-pk')
    rows = rows.annotate(
        _latest_profit=Subquery(latest.values('profit')[:1]),
        _latest_roi=Subquery(latest.values('roi')[:1]),
        _latest_cost=Subquery(latest.values('cost_snapshot')[:1]),
    )
    # All visible values for audited rows are taken from this audit, including
    # purchase cost, net profit and ROI. Filtering uses those SAME values.
    # The default view requires audit evidence; legacy rechecked=0 is allowed
    # to show snapshots that have never had a recheck.
    audited = Q(_latest_roi__isnull=False)
    legacy = Q(_latest_roi__isnull=True) if request.GET.get('rechecked') == '0' and not source else Q(pk__in=[])
    rows = rows.filter(
        (audited & Q(_latest_roi__gte=MIN_PROFITABLE_ROI) &
         Q(_latest_cost__gte=MIN_PROFITABLE_COST))
        |
        (legacy & Q(roi_snapshot__gte=MIN_PROFITABLE_ROI) &
         Q(cost_snapshot__gte=MIN_PROFITABLE_COST))
    )
    from .buying_history_display import buying_history, product_key
    purchase_history, own_stock, order_choices = buying_history(request.user)
    request.buying_order_choices = order_choices
    selected_order = next((x['value'] for x in order_choices if x['value'] == request.GET.get('purchased')), None)
    purchased_filter = request.GET.get('purchased')
    stock_filter = request.GET.get('own_stock')
    if selected_order or purchased_filter in ('yes', 'no') or stock_filter in ('yes', 'no'):
        matching = []
        for pk, brand, article in rows.values_list('pk', 'offer__brand', 'offer__article').iterator(chunk_size=1000):
            key = product_key(brand, article)
            bought = bool(purchase_history.get(key))
            if selected_order and not any(f"order:{x['pk']}" == selected_order for x in purchase_history.get(key, [])):
                continue
            balance = own_stock.get(key)
            if purchased_filter == 'yes' and not bought or purchased_filter == 'no' and bought:
                continue
            if stock_filter == 'yes' and (balance is None or balance <= 0):
                continue
            if stock_filter == 'no' and balance != 0:
                continue
            matching.append(pk)
        rows = rows.filter(pk__in=matching)
    # Approved 21.11.2026: highest net percentage return first across the
    # FULL filtered queryset (not merely the current page). Net profit in
    # tenge is the tie-breaker; stable ID makes pagination deterministic.
    roi_order = Coalesce('_latest_roi', 'roi_snapshot').desc()
    profit_order = Coalesce('_latest_profit', 'profit_snapshot').desc()
    rows = rows.order_by(profit_order, roi_order, 'pk') if request.GET.get('sort') == 'profit' else rows.order_by(roi_order, profit_order, 'pk')
    paginator = Paginator(rows, 200 if eighth_two_hundred else 153 if final_remaining else 100 if (next_hundred or fourth_hundred or fifth_hundred or sixth_hundred or seventh_hundred) else 50 if next_fifty else 40)
    page = paginator.get_page(request.GET.get('page'))
    items = list(page.object_list)
    offer_ids = [x.offer_id for x in items]
    candidate_map = {
        x.product_id: x for x in KaspiBuyingCandidate.objects.filter(
            product_id__in=[i.product_id for i in items if i.product_id]
        )
    }
    audits = {}
    display_audits = KaspiPriceAudit.objects.filter(offer_id__in=offer_ids)
    if source:
        display_audits = display_audits.filter(source_filename=source)
    for audit in display_audits.order_by('offer_id', '-observed_at', '-pk'):
        audits.setdefault(audit.offer_id, audit)
    previous_audit = {}
    for offer_id, audit in audits.items():
        previous_audit[offer_id] = KaspiPriceAudit.objects.filter(
            offer_id=offer_id, pk__lt=audit.pk).order_by('-pk').values_list(
                'min_price', flat=True).first()
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
            # Status is scoped to the current source and selected card. Never
            # infer an approved match from a different saved price audit.
            row['review_identity_status'] = audit.identity_status
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
            prior_price = previous_audit.get(offer.pk)
            row['apify_change_status'] = ('first' if prior_price is None else
                                         'changed' if audit.min_price != prior_price else 'unchanged')
            row['apify_previous_audit_price'] = prior_price
            row['audit_file'] = audit.source_filename
            row['audit_observed_at'] = audit.observed_at
        key = product_key(offer.brand, offer.article)
        row['purchase_history'] = purchase_history.get(key, [])
        row['own_stock'] = own_stock.get(key)
        result.append(row)
    page.object_list = result
    request.catalog_page = page
    request.catalog_total = paginator.count
    return result
