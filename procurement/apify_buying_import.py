"""Conservative Omega Apify import with mandatory net ROI gate before saving.

The review archive is separate from the buying-candidate table. Unprofitable
and uncalculable cards cannot create or overwrite buying candidates.
"""
import hashlib
import json
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import PurePath
from urllib.parse import urlsplit

from .suppliers import ACTIVE_SUPPLIER_NAMES

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Value
from django.db.models.functions import NullIf

from .models import KaspiBuyingCandidate, Offer, Preferences, PermanentBuyingItem
from .catalog_paging import (MIN_PROFITABLE_ROI, MIN_CONSUMABLE_COST,
                             MIN_PROFITABLE_COST, is_consumable, net_buying_profit)
from .omega_policy import POSITIVE_STOCK_PATTERN
import re as _re

PRODUCT_ID_RE = re.compile(r'-(\d{6,15})/?$')


def _money(value):
    if value is None or value == '':
        return None
    try:
        v = Decimal(str(value).replace(' ', '').replace(',', '.'))
        if not v.is_finite() or v < 0 or v > Decimal('999999999999'):
            return None
        return v.quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _token(value):
    """Case-insensitive exact comparison; preserve article separators and zeros."""
    return str(value or '').strip().casefold()


def _item_id(row):
    url = str(row.get('url') or row.get('productUrl') or '').strip()
    product_id = str(row.get('id') or row.get('productId') or '').strip()
    if not product_id:
        m = PRODUCT_ID_RE.search(urlsplit(url).path)
        product_id = m.group(1) if m else ''
    if product_id and re.fullmatch(r'\d{6,15}', product_id):
        return product_id
    if url.startswith('https://kaspi.kz/shop/p/'):
        return 'url-' + hashlib.sha256(url.encode()).hexdigest()[:32]
    return ''


def _normalize_rows(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ('entries', 'items', 'results', 'data'):
            if isinstance(payload.get(key), list):
                return payload[key]
    raise ValidationError('Нужен JSON-массив результатов Apify либо объект с массивом entries/items/results.')


def _offer_index(brands):
    """Index exact catalogue brands only; do not guess matches from similar part names."""
    index = defaultdict(list)
    if not brands:
        return index
    # This upload workflow is explicitly limited to Omega. Other suppliers'
    # existing records remain untouched and available elsewhere.
    from django.db.models import Q
    brand_filter = Q()
    for brand in brands:
        brand_filter |= Q(brand__iexact=brand)
    for offer in Offer.objects.filter(
        brand_filter, supplier__name__in=ACTIVE_SUPPLIER_NAMES
    ).only('id', 'brand', 'article', 'name', 'kaspi_url', 'match_verified',
           'cost', 'stock', 'category').iterator(chunk_size=1500):
        if offer.article:
            index[_token(offer.brand)].append(offer)
    return index


def import_apify_results(upload, filename, user=None):
    if upload.size > 50 * 1024 * 1024:
        raise ValidationError('JSON должен быть не более 50 МБ.')
    if not str(filename).lower().endswith('.json'):
        raise ValidationError('Загрузите файл JSON, экспортированный из Apify.')
    try:
        payload = json.loads(upload.read().decode('utf-8-sig'))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError('Файл не является корректным JSON.') from exc
    raw = _normalize_rows(payload)
    if not raw or len(raw) > 50000:
        raise ValidationError('Файл пустой либо содержит более 50 000 записей.')
    # Apify's multi-card search contains unrelated results. Recheck exports
    # have searchQuery per card and must use the separate, non-destructive
    # cross-card minimum-price aggregation path (never old first-card import).
    if user is not None and (any(
            isinstance(item, dict) and item.get('searchQuery') for item in raw)
            or Offer.objects.filter(supplier__name__in=[
                name for name in ACTIVE_SUPPLIER_NAMES if name != 'Omega']).exists()):
        from .apify_price_recheck import import_apify_recheck_results
        return import_apify_recheck_results(raw, filename, user)
    grouped = {}
    invalid = 0
    for row in raw:
        if not isinstance(row, dict):
            invalid += 1
            continue
        product_id = _item_id(row)
        if not product_id:
            invalid += 1
            continue
        product = grouped.setdefault(product_id, {
            'id': product_id, 'title': '', 'brand': '', 'url': '', 'cities': {},
            'prices': [], 'seller_prices': [],
        })
        product['title'] = str(row.get('title') or row.get('name') or product['title'])[:500]
        product['brand'] = str(row.get('brand') or product['brand'])[:120]
        product['url'] = str(row.get('url') or row.get('productUrl') or product['url'])[:2048]
        city = str(row.get('city') or 'Город не указан')[:100]
        city_id = str(row.get('cityId') or '')
        prices = []
        for key in ('minPrice', 'price'):
            price = _money(row.get(key))
            if price is not None:
                prices.append(price)
        offers = row.get('offers') or row.get('sellers') or []
        if not isinstance(offers, list):
            offers = []
        sellers = []
        for seller in offers[:200]:
            if not isinstance(seller, dict):
                continue
            cost = _money(seller.get('price'))
            if cost is None:
                continue
            prices.append(cost)
            product['seller_prices'].append(cost)
            sellers.append({
                'name': str(seller.get('sellerName') or seller.get('name') or '')[:180],
                'price': str(cost),
                'delivery_cost': seller.get('deliveryCost'),
                'delivery_days': seller.get('deliveryDays'),
            })
        product['prices'].extend(prices)
        product['cities'][city] = {
            'city': city, 'city_id': city_id,
            'min_price': str(min(prices)) if prices else None,
            'sellers': sellers,
            'sellers_count': row.get('sellersCount'),
        }
    if not grouped:
        raise ValidationError('В JSON нет распознаваемых карточек Kaspi с идентификаторами.')
    brands = {p['brand'] for p in grouped.values() if p['brand']}
    index = _offer_index(brands)
    candidates = []
    prefs = Preferences.objects.filter(user=user).first() if user is not None else None
    commission = prefs.commission if prefs else None
    expenses = prefs.expenses if prefs else None
    counts = {'rows': len(raw), 'products': len(grouped), 'invalid': invalid,
              'linked': 0, 'verified': 0, 'unmatched': 0,
              'rejected_below_50': 0, 'rejected_unverifiable': 0,
              'rejected_unconfirmed_price': 0}
    for p in grouped.values():
        brand = _token(p['brand'])
        # Exactly the same article token, not a substring (e.g. 1234 != 12345).
        # Do not collapse punctuation: AB-123 and AB123 are different SKUs.
        # Standalone complete title tokens are hints, not proof of identity.
        article_tokens = {_token(token.strip('()[]{}:,;')) for token in p['title'].split()}
        linked = [
            offer for offer in index.get(brand, [])
            if _token(offer.article) in article_tokens
        ]
        # One Kaspi card is linked to the cheapest supplier entry of the exact
        # same brand+article; no title, fitment or category judgement here.
        linked.sort(key=lambda offer: (offer.cost, offer.pk))
        match = linked[0] if linked else None
        verified = bool(match and match.match_verified and
                        _item_id({'url': match.kaspi_url}) == p['id'])
        status = 'verified' if verified else 'candidate' if match else 'unmatched'
        counts['verified' if verified else 'linked' if match else 'unmatched'] += 1
        # Do not import sub-50% products into the candidate database at all.
        # Missing supplier identity, stock, price, or configured costs cannot
        # be treated as an eligible profitable purchase.
        if match is None or commission is None or expenses is None or not p['prices']:
            counts['rejected_unverifiable'] += 1
            continue
        stock = str(match.stock or '')
        if not _re.match(POSITIVE_STOCK_PATTERN, stock):
            counts['rejected_unverifiable'] += 1
            continue
        product_type = ' '.join((match.name or '', match.category or '', p['title']))
        if any(x in product_type.casefold() for x in ('пружин', 'spring')):
            counts['rejected_unverifiable'] += 1
            continue
        if is_consumable(match.name, match.category):
            if match.cost < MIN_CONSUMABLE_COST:
                counts['rejected_unverifiable'] += 1
                continue
        elif match.cost <= MIN_PROFITABLE_COST:
            counts['rejected_unverifiable'] += 1
            continue
        # Top-level minPrice is a card/search observation, not proof of
        # the lowest available seller offer. For NEW buying opportunities,
        # accept only actual seller-offer prices from the Apify payload.
        if not p['seller_prices']:
            counts['rejected_unconfirmed_price'] += 1
            continue
        minimum_price = min(p['seller_prices'])
        profit = net_buying_profit(minimum_price, match.cost, commission, expenses)
        if profit is None:
            counts['rejected_unverifiable'] += 1
            continue
        if profit < match.cost * MIN_PROFITABLE_ROI / Decimal('100'):
            counts['rejected_below_50'] += 1
            continue
        # Permanent acquisition ledger is strictly append-only: an imported
        # price can never rewrite an earlier approved opportunity.
        if user is not None:
            with transaction.atomic():
                PermanentBuyingItem.objects.get_or_create(
                    offer_id=match.pk,
                    defaults={
                        'source': 'apify',
                        'product_id': p['id'],
                        'title': p['title'] or match.name,
                        'kaspi_url': p['url'],
                        'cost_snapshot': match.cost,
                        'kaspi_price_snapshot': minimum_price,
                        'commission_snapshot': commission,
                        'expenses_snapshot': max(Decimal('250'), expenses),
                        'profit_snapshot': profit.quantize(Decimal('.01')),
                        'roi_snapshot': (profit * Decimal('100') / match.cost).quantize(Decimal('.0001')),
                        'identity_status': status,
                        'photo_url': '',  # See source photo only after a verified identity.
                    },
                )
        candidates.append(KaspiBuyingCandidate(
            product_id=p['id'], title=p['title'], brand=p['brand'], url=p['url'],
            minimum_price=minimum_price,
            city_offers=list(p['cities'].values()), offer=match,
            match_status=status, source_filename=PurePath(filename).name[:255],
        ))
    # An already verified card must not be downgraded or rebound by a new
    # import. Preserve its identity and commercial values for manual review.
    protected_ids = set(KaspiBuyingCandidate.objects.filter(
        product_id__in=[candidate.product_id for candidate in candidates],
        match_status='verified',
    ).values_list('product_id', flat=True))
    candidates = [candidate for candidate in candidates
                  if candidate.product_id not in protected_ids]
    counts['protected_verified'] = len(protected_ids)
    with transaction.atomic():
        if candidates:
            KaspiBuyingCandidate.objects.bulk_create(
            candidates, batch_size=300, update_conflicts=True,
            unique_fields=['product_id'],
            update_fields=['title', 'brand', 'url', 'minimum_price', 'city_offers',
                           'offer', 'match_status', 'source_filename', 'imported_at'],
        )
    return counts


def ranked_buying_candidates(user):
    prefs, _ = Preferences.objects.get_or_create(user=user)
    qs = KaspiBuyingCandidate.objects.select_related('offer', 'offer__supplier')
    commission = prefs.commission
    expenses = prefs.expenses
    if commission is None or expenses is None:
        return qs.order_by('-imported_at', 'pk'), False
    cost_field = DecimalField(max_digits=20, decimal_places=5)
    # Packaging is included once, not in addition to the configured expenses.
    expense = max(Decimal('250'), expenses)
    margin = ExpressionWrapper(
        F('minimum_price') * (Value(Decimal('1')) - Value(commission) / Value(Decimal('100')))
        - F('offer__cost') - Value(expense),
        output_field=cost_field,
    )
    qs = qs.annotate(
        calculated_profit=margin,
        calculated_roi=ExpressionWrapper(
            margin * Value(Decimal('100')) / NullIf(F('offer__cost'), Value(Decimal('0'))),
            output_field=cost_field,
        ),
    ).order_by(F('calculated_roi').desc(nulls_last=True),
               F('calculated_profit').desc(nulls_last=True), 'pk')
    return qs, True
