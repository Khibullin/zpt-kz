"""Existing profitable-buying screen: show every matched Apify card, plus saved deals.

A Kaspi card is a display row; several cards may point at one supplier Offer.
No source Offer, warehouse, price or verification flag is mutated.
"""
from datetime import timedelta
from decimal import Decimal
from .suppliers import ACTIVE_SUPPLIER_NAMES

from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
from .models import KaspiBuyingCandidate, Offer, Favorite, KaspiPreparedCard, RetailListing, VehicleFitment
from .analysis import evaluate, PACKAGING_COST, stock_status
from .catalog_paging import cumulative_profitable_query
from .fitment_filters import matches_configuration
from .vehicle_countries import country_makes
from .catalog_paging import (profitable_product_exclusions, buying_cost_query,
                             net_buying_profit, MIN_PROFITABLE_ROI)
from .omega_policy import POSITIVE_STOCK_PATTERN


def filtered_offer_ids(filters):
    """Apply real vehicle-fitment and demand filters to both Kaspi and legacy rows.

    Keep the baseline unfiltered, and use a DB subquery rather than holding
    the entire catalogue of supplier offers in RAM.
    """
    keys = ('vehicle_country', 'vehicle_make', 'vehicle_model',
            'vehicle_generation', 'vehicle_year', 'selected_years', 'fitment', 'demand')
    if not any(filters.get(key) for key in keys):
        return None
    offers = Offer.objects.all()
    fitment_filters = {'fitments__verified': True}
    if filters.get('vehicle_country'):
        fitment_filters['fitments__make__in'] = country_makes(filters['vehicle_country'].pk)
    if filters.get('vehicle_make'):
        fitment_filters['fitments__make'] = filters['vehicle_make']
    if filters.get('vehicle_model'):
        fitment_filters['fitments__model'] = filters['vehicle_model']
    if (any(filters.get(key) for key in ('vehicle_country', 'vehicle_make', 'vehicle_model'))
            or filters.get('fitment') == 'verified'):
        offers = offers.filter(**fitment_filters).distinct()
    if filters.get('vehicle_generation') or filters.get('vehicle_year') or filters.get('selected_years'):
        related = {key.removeprefix('fitments__'): value for key, value in fitment_filters.items()}
        fitments = VehicleFitment.objects.filter(offer__in=offers.values('pk'), **related)
        eligible = {
            fitment.offer_id
            for fitment in fitments.only('offer_id', 'configurations').iterator(chunk_size=100)
            if matches_configuration(
                fitment.configurations, filters.get('vehicle_generation', ''),
                filters.get('vehicle_year'), filters.get('selected_years'),
            )
        }
        offers = offers.filter(pk__in=eligible)
    if filters.get('fitment') == 'unknown':
        offers = offers.exclude(fitments__verified=True)
    demand = filters.get('demand')
    if demand:
        today = timezone.localdate()
        fresh = Q(evidence_date__gte=today - timedelta(days=7), evidence_date__lte=today)
        sales = fresh & Q(sales_30__gt=0)
        reviews = fresh & Q(recent_reviews__gt=0)
        if demand == 'sales':
            offers = offers.filter(sales)
        elif demand == 'reviews':
            offers = offers.filter(reviews)
        elif demand == 'unknown':
            offers = offers.exclude(sales | reviews)
    return offers.values('pk')


def visible_buying_rows(request, preferences, filters):
    supplier = filters.get('supplier')
    offer_scope = filtered_offer_ids(filters)
    # 21.11.2026 exact brand+article Apify observations passed the
    # conservative ROI gate, but fulfillment is still awaiting verification.
    # Do not widen visibility for any other unverified candidate.
    qs = KaspiBuyingCandidate.objects.filter(
        Q(match_status='verified') |
        Q(match_status='candidate',
          source_filename='dataset_kaspi-api_2026-11-21_13-39-32-482(2).json'),
        offer__isnull=False, offer__supplier__name__in=ACTIVE_SUPPLIER_NAMES,
    )
    qs = qs.filter(buying_cost_query('offer__'), offer__stock__regex=POSITIVE_STOCK_PATTERN).exclude(profitable_product_exclusions('offer__'))
    if offer_scope is not None:
        qs = qs.filter(offer_id__in=offer_scope)
    if supplier:
        qs = qs.filter(offer__supplier=supplier)
    if filters.get('brand'):
        qs = qs.filter(offer__brand__icontains=filters['brand'])
    if filters.get('category'):
        qs = qs.filter(offer__category=filters['category'])
    if filters.get('q'):
        q = filters['q'].strip()
        qs = qs.filter(Q(offer__article__icontains=q) | Q(offer__brand__icontains=q)
                       | Q(offer__name__icontains=q) | Q(offer__applicability__icontains=q)
                       | Q(title__icontains=q))
    if filters.get('max_cost') is not None:
        qs = qs.filter(offer__cost__lte=filters['max_cost'])

    if filters.get('favorites'):
        favored = Favorite.objects.filter(user=request.user).values('offer_id')
        qs = qs.filter(offer_id__in=favored)
    if filters.get('availability'):
        # Availability is evaluated from the exact supplier text, without
        # treating missing stock as available.
        allowed_ids = [pk for pk, stock in
                       qs.values_list('offer_id', 'offer__stock').distinct()
                       if stock_status(stock) == filters['availability']]
        qs = qs.filter(offer_id__in=allowed_ids)

    # Keep the Kaspi-card identity as the row key, never DISTINCT(offer_id).
    # Large nested seller snapshots are only needed for seller-count filtering.
    # Without that filter, read them only for the 40 visible cards below.
    bounds = filters.get('seller_bounds')
    candidate_fields = [
        'id', 'offer_id', 'title', 'minimum_price', 'url', 'match_status',
        'offer__cost', 'offer__supplier_id',
    ]
    if bounds is not None:
        candidate_fields.append('city_offers')
    candidates = list(qs.values(*candidate_fields))
    candidate_offer_ids = set()
    commission = preferences.commission
    expenses = preferences.expenses
    extra_cost = max(PACKAGING_COST, expenses) if expenses is not None else None

    def calculation(price, cost):
        profit = net_buying_profit(price, cost, commission, expenses)
        roi = (profit * Decimal('100') / cost if profit is not None else None)
        return profit, roi

    indexed = []
    for candidate in candidates:
        if bounds is not None:
            counts = []
            for city in candidate['city_offers'] or []:
                if not isinstance(city, dict):
                    continue
                n = city.get('sellers_count')
                if isinstance(n, int) and not isinstance(n, bool) and n >= 0:
                    counts.append(n)
                elif isinstance(n, str) and n.isdecimal():
                    counts.append(int(n))
                elif isinstance(city, dict) and isinstance(city.get('sellers'), list):
                    # The source supplies the observed seller list but usually
                    # omits sellersCount. Never pretend it is an all-Kaspi total.
                    counts.append(len(city['sellers']))
            if not counts or not bounds[0] <= max(counts) <= bounds[1]:
                continue
        profit, roi = calculation(candidate['minimum_price'], candidate['offer__cost'])
        if roi is None or roi < MIN_PROFITABLE_ROI:
            continue
        if filters.get('min_profit') is not None and (profit is None or profit < filters['min_profit']):
            continue
        if filters.get('min_markup') is not None and (roi is None or roi < filters['min_markup']):
            continue
        candidate_offer_ids.add(candidate['offer_id'])
        indexed.append(('apify', candidate, profit, roi))

    # Previously saved profitable offers must not disappear on Apify import.
    # Include currently qualified legacy offers without duplicating an Apify supplier row.
    old = Offer.objects.filter(buying_cost_query(), stock__regex=POSITIVE_STOCK_PATTERN,
                               supplier__name__in=ACTIVE_SUPPLIER_NAMES, match_verified=True).exclude(
        profitable_product_exclusions()
    ).select_related(
        'supplier', 'profitable_record',
    ).defer(
        'description', 'applicability', 'characteristics', 'photo_url', 'supplier_url'
    ).exclude(pk__in=candidate_offer_ids)
    if bounds is not None:
        old = old.select_related('kaspi_sellers')
    if offer_scope is not None:
        old = old.filter(pk__in=offer_scope)
    if supplier:
        old = old.filter(supplier=supplier)
    if filters.get('brand'):
        old = old.filter(brand__icontains=filters['brand'])
    if filters.get('category'):
        old = old.filter(category=filters['category'])
    if filters.get('q'):
        q = filters['q'].strip()
        old = old.filter(Q(article__icontains=q) | Q(brand__icontains=q)
                         | Q(name__icontains=q) | Q(applicability__icontains=q))
    if filters.get('max_cost') is not None:
        old = old.filter(cost__lte=filters['max_cost'])
    if filters.get('favorites'):
        old = old.filter(pk__in=Favorite.objects.filter(user=request.user).values('offer_id'))
    if filters.get('availability'):
        old = old.filter(pk__in=[
            pk for pk, stock in old.values_list('pk', 'stock')
            if stock_status(stock) == filters['availability']
        ])
    legacy = cumulative_profitable_query(old, preferences)
    for offer in legacy.iterator(chunk_size=500):
        if bounds is not None:
            # Seller-count filters also apply to historical offers. Only use
            # an observed, complete snapshot for the matching Kaspi card;
            # unknown counts must not silently pass the filter.
            snapshot = getattr(offer, 'kaspi_sellers', None)
            if not (snapshot and snapshot.complete and snapshot.city == '750000000'
                    and snapshot.source_url == offer.kaspi_url
                    and isinstance(snapshot.sellers, list)
                    and bounds[0] <= len(snapshot.sellers) <= bounds[1]):
                continue
        record = getattr(offer, 'profitable_record', None)
        price = (record.source_kaspi_price if record else offer.kaspi_price)
        cost = (record.source_cost if record else offer.cost)
        profit, roi = calculation(price, cost)
        if record:
            # Keep archived prices/dates, but apply current buying profitability.
            profit, roi = calculation(price, cost)
        if roi is None or roi < MIN_PROFITABLE_ROI:
            continue
        if filters.get('min_profit') is not None and (profit is None or profit < filters['min_profit']):
            continue
        if filters.get('min_markup') is not None and (roi is None or roi < filters['min_markup']):
            continue
        indexed.append(('legacy', (offer.pk, cost, price, record), profit, roi))

    # Approved ordering: highest net ROI percentage first, then tenge profit.
    indexed.sort(key=lambda r: (r[3] is None, -(r[3] or Decimal('0')),
                                r[2] is None, -(r[2] or Decimal('0')),
                                r[1]['id'] if r[0] == 'apify' else r[1][0]))
    paginator = Paginator(indexed, 40)
    page = paginator.get_page(request.GET.get('page'))
    # Fetch heavyweight city-level seller data only for the visible page.
    if bounds is None:
        visible_candidate_ids = [
            entry[1]['id'] for entry in page.object_list if entry[0] == 'apify'
        ]
        city_data = dict(KaspiBuyingCandidate.objects.filter(
            pk__in=visible_candidate_ids,
        ).values_list('pk', 'city_offers'))
        for entry in page.object_list:
            if entry[0] == 'apify':
                entry[1]['city_offers'] = city_data.get(entry[1]['id']) or []
    offer_ids = [entry[1]['offer_id'] if entry[0] == 'apify' else entry[1][0]
                 for entry in page.object_list]
    offers = {o.pk: o for o in Offer.objects.filter(pk__in=offer_ids).select_related(
        'supplier', 'kaspi_sellers', 'kaspi_card_search'
    )}
    favorite_ids = set(Favorite.objects.filter(
        user=request.user, offer_id__in=offer_ids,
    ).values_list('offer_id', flat=True))
    prepared_photo = {
        item['offer_id']: item['photo_url']
        for item in KaspiPreparedCard.objects.filter(offer_id__in=offer_ids)
        .exclude(photo_url='').values('offer_id', 'photo_url')
    }
    retail_photos = {}
    for item in RetailListing.objects.filter(offer_id__in=offer_ids).values(
        'offer_id', 'source_content'
    ):
        source = item['source_content'] if isinstance(item['source_content'], dict) else {}
        images = source.get('images') or []
        if isinstance(images, list) and images and isinstance(images[0], str) and images[0].startswith('https://'):
            retail_photos[item['offer_id']] = images[0]
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
                elif isinstance(city, dict) and isinstance(city.get('sellers'), list):
                    # The source supplies the observed seller list but usually
                    # omits sellersCount. Never pretend it is an all-Kaspi total.
                    counts.append(len(city['sellers']))
            count = max(counts) if counts else None
            candidate = {'id': payload['id'], 'title': payload['title'], 'url': payload['url'],
                         'seller_count': count, 'match_status': payload['match_status']}
            cost = offer.cost
        else:
            candidate = None
            cost, price = payload[1], payload[2]
        # Historical snapshots retain their original commission and expenses;
        # today's account settings must not rewrite the explanation of old profits.
        historical_record = payload[3] if kind == 'legacy' else None
        effective_commission = historical_record.commission if historical_record else commission
        effective_expenses = historical_record.expenses if historical_record else extra_cost
        analysis['commission_rate'] = effective_commission
        analysis['expenses'] = effective_expenses
        if price is not None and effective_commission is not None:
            after_commission = price * (Decimal('1') - effective_commission / Decimal('100'))
            analysis['commission_amount'] = price - after_commission
            analysis['kaspi_after_commission'] = after_commission
            analysis['difference'] = after_commission - cost
        else:
            analysis['commission_amount'] = None
            analysis['kaspi_after_commission'] = None
            analysis['difference'] = None
        firsthand = (offer.characteristics or {}).get('firsthand_omega_20261117', {})
        firsthand_images = firsthand.get('images') if isinstance(firsthand, dict) else []
        firsthand_image = (
            firsthand_images[0] if isinstance(firsthand_images, list)
            and firsthand_images and isinstance(firsthand_images[0], str)
            and firsthand_images[0].startswith('https://') else ''
        )
        fallback_image = (
            prepared_photo.get(offer.pk, '') or retail_photos.get(offer.pk, '')
        )
        display_photo = offer.photo_url or fallback_image or firsthand_image
        preliminary_photo = bool(display_photo and not offer.photo_url and not fallback_image)
        analysis.update({
            'offer': offer, 'candidate': candidate,
            'favorite': offer.pk in favorite_ids,
            'display_photo_url': display_photo,
            'preliminary_photo': preliminary_photo,
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
            'kaspi_status': 'Карточка из Apify; проверка исполнения не завершена' if candidate else 'Сохранённое предложение Kaspi',
            'kaspi_checked_on': None if candidate else offer.kaspi_price_checked_on,
        })
        rows.append(analysis)
    page.object_list = rows
    request.catalog_page = page
    request.catalog_total = paginator.count
    request.apify_visible_total = sum(item[0] == 'apify' for item in indexed)
    return rows
