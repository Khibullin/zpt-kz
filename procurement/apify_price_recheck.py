"""Conservative repeated Apify-price comparison by exact SKU.

Search results frequently include unrelated products. The searchQuery describes
what Apify searched for, NOT what the result actually is: a matching product
can be returned in a neighboring article's result list. Match only the
product's own brand and complete article. For each
Omega SKU compare seller offers on all returned cards, including numeric article
variants differing only in leading zeros. Execution must be checked manually.

This module NEVER changes source Offer, historic PermanentBuyingItem, Selection
or previous KaspiBuyingCandidate. A per-file audit row is updated to the lowest
observed seller price across consecutive 500-card batches.
"""
import re
from collections import defaultdict
from decimal import Decimal
from pathlib import PurePath
from urllib.parse import urlsplit

from .suppliers import ACTIVE_SUPPLIER_NAMES

from django.db import transaction
from django.db.models import Q

from .analysis import PACKAGING_COST
from .catalog_paging import net_buying_profit
from .models import BuyingComparisonMaterial, KaspiPriceAudit, Offer, Preferences
from django.utils import timezone
from .omega_policy import POSITIVE_STOCK_PATTERN


def normalize_article(value):
    """Ignore owner-approved dash/space formatting, preserving full SKU suffixes."""
    value = re.sub(r'[\s\-\u2010-\u2015\u2212]+', '', str(value or '')).casefold()
    if value.isascii() and value.isdecimal():
        return value.lstrip('0') or '0'
    return value


def article_tokens(title):
    """Whole tokens and spaced article variants; never match SKU substrings.

    Keep slash/dot and L/R. Join at most six neighbouring word tokens for OCR
    and Kaspi formatting such as ML 9108R or ML – 9108R.
    """
    parts = re.findall(r'[^\W_]+(?:[-\u2010-\u2015\u2212/.][^\W_]+)*',
                       str(title or '').casefold(), re.UNICODE)
    result = {normalize_article(part) for part in parts}
    for start in range(len(parts)):
        for end in range(start + 2, min(len(parts), start + 6) + 1):
            candidate = ''.join(parts[start:end])
            if len(candidate) <= 120 and re.search(r'\d', candidate):
                result.add(normalize_article(candidate))
    return result


def _brand(value):
    value = str(value or '').strip().casefold()
    # Explicit manufacturer alias, corroborated by the supplier's own seller SKU.
    return 'parts-mall' if value in ('pmc parts mall', 'parts mall', 'parts-mall') else value


def _money(value):
    try:
        price = Decimal(str(value))
        if price.is_finite() and 0 < price < Decimal('1000000000000'):
            return price.quantize(Decimal('.01'))
    except (TypeError, ValueError, ArithmeticError):
        pass
    return None


def _offer_index(brands):
    index = defaultdict(list)
    if not brands:
        return index
    condition = Q(pk__in=[])
    for brand in brands:
        condition |= Q(brand__iexact=brand)
        if brand == 'parts-mall':
            condition |= Q(brand__iexact='PMC PARTS MALL') | Q(brand__iexact='PARTS MALL')
    for offer in Offer.objects.filter(
            condition, supplier__name__in=ACTIVE_SUPPLIER_NAMES).only(
            'id', 'brand', 'article', 'name', 'category',
            'cost', 'stock').iterator(chunk_size=1500):
        index[_brand(offer.brand)].append(offer)
    return index


def explicit_execution_conflict(supplier_title, card_title):
    """Reject only explicit contrary attributes; missing attributes stay unknown."""
    def attributes(title):
        text = str(title or '').casefold()
        text = re.sub(r'(?:лев\w*|прав\w*)\s+рул\w*', '', text)
        groups = [
            (r'\b(?:лев\w*|left|lh)\b', r'\b(?:прав\w*|right|rh)\b'),
            (r'\b(?:передн\w*|front)\b', r'\b(?:задн\w*|rear)\b'),
        ]
        result = [{i for i, pattern in enumerate(pair) if re.search(pattern, text)} for pair in groups]
        quantities = {int(n) for n in re.findall(r'\b(\d{1,3})\s*(?:шт\.?|pcs?)\b', text)}
        return result, quantities
    supplier, supplier_qty = attributes(supplier_title)
    card, card_qty = attributes(card_title)
    return any(a and b and a.isdisjoint(b) for a, b in zip(supplier, card)) or bool(
        supplier_qty and card_qty and supplier_qty.isdisjoint(card_qty))


def seller_evidence(row, seller, source):
    return {'source': source, 'product_id': str(row['id']),
            'card_url': str(row.get('url') or ''),
            'seller': str(seller.get('sellerName') or seller.get('name') or '')[:180],
            'seller_id': str(seller.get('sellerId') or seller.get('id') or '')[:180],
            'city': str(seller.get('city') or seller.get('cityId') or row.get('city') or row.get('cityId') or '')[:180],
            'observed_at': timezone.now().isoformat(),  # Compatibility: processing time.
            'price_observed_at': str(row.get('scrapedAt') or '')[:80],  # Never card createdAt.
            'coverage': 'returned_offers_only', 'policy': 'name_article_v4'}


def import_apify_recheck_results(raw, filename, user):
    preferences = Preferences.objects.filter(user=user).first()
    commission = preferences.commission if preferences else None
    expenses = preferences.expenses if preferences else None
    stats = {
        'rows': len(raw), 'products': 0, 'invalid': 0,
        'linked': 0, 'verified': 0, 'unmatched': 0, 'protected_verified': 0,
        'rejected_below_50': 0, 'rejected_unverifiable': 0,
        'rejected_unconfirmed_price': 0,
        'recheck_matched_cards': 0, 'recheck_price_cards': 0,
        'recheck_unrelated': 0, 'recheck_missing_sellers': 0,
        'recheck_offer_updates': 0,
    }
    if commission is None or expenses is None:
        raise ValueError('Для расчёта необходимо задать комиссию Kaspi и расходы.')
    brands = {_brand(row.get('brand')) for row in raw
              if isinstance(row, dict) and row.get('brand')}
    index = _offer_index(brands)
    grouped = {}
    for row in raw:
        if not isinstance(row, dict) or not row.get('id'):
            stats['invalid'] += 1
            continue
        stats['products'] += 1
        brand = _brand(row.get('brand'))
        title = str(row.get('title') or '')
        try:
            url = urlsplit(str(row.get('url') or ''))
        except ValueError:
            stats['invalid'] += 1
            continue
        url_id = re.search(r'-(\d{6,15})/?$', url.path)
        if (url.scheme != 'https' or url.hostname != 'kaspi.kz'
                or not url.path.startswith('/shop/p/') or not url_id
                or url_id.group(1) != str(row['id'])
                or row.get('currency', 'KZT') != 'KZT'):
            stats['invalid'] += 1
            continue
        tokens = article_tokens(title)
        matching = [offer for offer in index.get(brand, [])
                    if normalize_article(offer.article) in tokens
                    and not explicit_execution_conflict(offer.name, title)]
        if not matching or len({normalize_article(offer.article) for offer in matching}) != 1:
            stats['unmatched'] += 1
            stats['recheck_unrelated'] += 1
            continue
        for offer in matching:
            stats['linked'] += 1
            stats['recheck_matched_cards'] += 1
            if not re.match(POSITIVE_STOCK_PATTERN, str(offer.stock or '')):
                stats['rejected_unverifiable'] += 1
                continue
            if re.search(r'пружин|spring', ' '.join((
                    offer.name or '', offer.category or '', title)), re.I):
                stats['rejected_unverifiable'] += 1
                continue
            sellers = row.get('offers') or row.get('sellers') or []
            if not isinstance(sellers, list):
                sellers = []
            priced = [(price, seller)
                      for seller in sellers if isinstance(seller, dict)
                      and seller.get('inStock') is not False
                      and seller.get('currency', 'KZT') == 'KZT'
                      for price in [_money(seller.get('price'))] if price]
            if not priced or row.get('inStock') is False:
                stats['rejected_unconfirmed_price'] += 1
                stats['recheck_missing_sellers'] += 1
                continue
            stats['recheck_price_cards'] += 1
            price, seller = min(priced, key=lambda value: value[0])
            evidence = seller_evidence(row, seller, PurePath(filename).name[:255])
            seller_name = evidence['seller']
            product_id = str(row['id'])[:80]
            key = offer.pk
            if key not in grouped:
                grouped[key] = {'offer': offer, 'minimum': price, 'seller': seller_name,
                                'product_id': product_id, 'title': title[:500],
                                'url': str(row.get('url') or '')[:2048],
                                'card_ids': set(), 'evidence': evidence}
            if price < grouped[key]['minimum']:
                grouped[key].update(minimum=price, seller=seller_name,
                                    product_id=product_id, title=title[:500],
                                    url=str(row.get('url') or '')[:2048], evidence=evidence)
            grouped[key]['card_ids'].add(product_id)

    source = PurePath(filename).name[:255]
    cost_expenses = max(PACKAGING_COST, Decimal(expenses))
    with transaction.atomic():
        for entry in grouped.values():
            offer = entry['offer']
            price = entry['minimum']
            profit = net_buying_profit(price, offer.cost, commission, expenses)
            if profit is None:
                continue
            defaults = dict(
                min_price=price, product_id=entry['product_id'],
                card_url=entry['url'], card_title=entry['title'],
                seller_name=entry['seller'], cost_snapshot=offer.cost,
                commission_snapshot=commission,
                expenses_snapshot=cost_expenses,
                profit=profit.quantize(Decimal('.01')),
                roi=(profit * Decimal(100) / offer.cost).quantize(Decimal('.0001')),
                card_ids=sorted(entry['card_ids']), price_evidence=entry['evidence'],
                identity_status='needs_review',
            )
            record, created = KaspiPriceAudit.objects.select_for_update().get_or_create(
                offer=offer, source_filename=source, defaults=defaults)
            if not created:
                from .comparison_screenshot_ai import screenshot_price_applies
                materials = BuyingComparisonMaterial.objects.filter(
                    offer=offer, source_filename=source, kaspi_product_id=record.product_id)
                if any(screenshot_price_applies(material, record) for material in materials):
                    stats['protected_verified'] += 1
                    continue
                price_changed = price < record.min_price
                previous = set(record.card_ids or [])
                combined = sorted(previous | entry['card_ids'])
                if entry['card_ids'] - previous:
                    record.minimum_review_status = 'needs_review'
                    record.minimum_reviewed_at = None
                    record.minimum_review_evidence = {}
                record.card_ids = combined
                # Freeze the commercial configuration of the first matching
                # batch; subsequent batches may only improve the minimum.
                if price < record.min_price:
                    record.minimum_review_status = 'needs_review'
                    record.minimum_reviewed_at = None
                    record.minimum_review_evidence = {}
                    record.identity_status = 'needs_review'
                    record.min_price = price
                    record.price_evidence = entry['evidence']
                    record.product_id = entry['product_id']
                    record.card_url = entry['url']
                    record.card_title = entry['title']
                    record.seller_name = entry['seller']
                    new_profit = net_buying_profit(
                        price, record.cost_snapshot,
                        record.commission_snapshot, record.expenses_snapshot)
                    record.profit = new_profit.quantize(Decimal('.01'))
                    record.roi = (new_profit * Decimal(100) /
                                  record.cost_snapshot).quantize(Decimal('.0001'))
                if entry['card_ids'] - previous or price_changed:
                    record.save()
            stats['recheck_offer_updates'] += 1
            if record.roi < Decimal('50'):
                stats['rejected_below_50'] += 1
    # Only the audit table is altered. No old product or purchase is changed.
    return stats
