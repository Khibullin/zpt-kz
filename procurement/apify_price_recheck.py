"""Conservative repeated Apify-price comparison by exact SKU.

Search results frequently include unrelated products. The searchQuery describes
what Apify searched for, NOT what the result actually is: a matching product
can be returned in a neighboring article's result list. Match only the
product's own brand and complete article. For each
Omega SKU compare seller offers on all returned cards, preserving every
character of the full article (including leading zeros). Execution must be checked manually.

This module NEVER changes source Offer, historic PermanentBuyingItem, Selection
or previous KaspiBuyingCandidate. A per-file audit row is updated to the lowest
observed seller price across consecutive 500-card batches.
"""
import re
from collections import defaultdict
from decimal import Decimal
from pathlib import PurePath
from urllib.parse import urlsplit

from django.db import transaction
from django.db.models import Q

from .analysis import PACKAGING_COST
from .catalog_paging import net_buying_profit
from .models import KaspiPriceAudit, Offer, Preferences
from .omega_policy import POSITIVE_STOCK_PATTERN


def normalize_article(value):
    """Case-insensitive exact full SKU: leading zeroes are significant."""
    return str(value or '').strip().casefold()


def article_tokens(title):
    """Complete word-sized tokens, preserving article separators and suffixes."""
    return {normalize_article(x) for x in re.findall(
        r'[^\W_]+(?:[-/.][^\W_]+)*', str(title or '').casefold(), re.UNICODE)}


def _brand(value):
    return str(value or '').strip().casefold()


def _money(value):
    try:
        price = Decimal(str(value))
        if price.is_finite() and 0 < price < Decimal('1000000000000'):
            return price.quantize(Decimal('.01'))
    except (TypeError, ValueError, ArithmeticError):
        pass
    return None


def _valid_one_piece_kaspi_card(row):
    """Reject unrelated, incomplete or untrusted card identity before audit."""
    parsed = urlsplit(str(row.get('url') or ''))
    match = re.search(r'-(\d{6,15})/?$', parsed.path)
    return bool(parsed.scheme == 'https' and parsed.hostname == 'kaspi.kz'
                and parsed.path.startswith('/shop/p/') and match
                and match.group(1) == str(row.get('id') or '')
                and row.get('currency') == 'KZT'
                and row.get('inStock') is True
                and re.search(r'(?<!\d)1\s*шт(?=\s|\.|$)',
                              str(row.get('title') or ''), re.I))


def _offer_index(brands):
    index = defaultdict(list)
    if not brands:
        return index
    condition = Q(pk__in=[])
    for brand in brands:
        condition |= Q(brand__iexact=brand)
    for offer in Offer.objects.filter(
            condition, supplier__name='Omega').only(
            'id', 'brand', 'article', 'name', 'category',
            'cost', 'stock').iterator(chunk_size=1500):
        index[_brand(offer.brand)].append(offer)
    return index


def import_apify_recheck_results(raw, filename, user):
    preferences = Preferences.objects.filter(user=user).first()
    commission = preferences.commission if preferences else None
    expenses = preferences.expenses if preferences else None
    stats = {
        'rows': len(raw), 'products': 0, 'invalid': 0,
        'linked': 0, 'verified': 0, 'unmatched': 0,
        'rejected_below_50': 0, 'rejected_unverifiable': 0,
        'rejected_unconfirmed_price': 0,
        'recheck_matched_cards': 0, 'recheck_price_cards': 0,
        'recheck_unrelated': 0, 'recheck_missing_sellers': 0,
        'recheck_duplicate_supplier_sku': 0,
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
        if not _valid_one_piece_kaspi_card(row):
            stats['invalid'] += 1
            continue
        brand = _brand(row.get('brand'))
        title = str(row.get('title') or '')
        tokens = article_tokens(title)
        matching = [offer for offer in index.get(brand, [])
                    if normalize_article(offer.article) in tokens]
        # Omega may have two Offer rows for the same case-insensitive
        # brand+article, with contradictory stock snapshots (e.g. old 0,
        # new >20). Never silently select one row or treat this as an
        # unrelated Kaspi card: require reconciliation of supplier identity.
        if len(matching) > 1:
            stats['unmatched'] += 1
            stats['recheck_duplicate_supplier_sku'] += 1
            continue
        if not matching:
            stats['unmatched'] += 1
            stats['recheck_unrelated'] += 1
            continue
        offer = matching[0]
        stats['linked'] += 1
        stats['recheck_matched_cards'] += 1
        if not re.match(POSITIVE_STOCK_PATTERN, str(offer.stock or '')):
            stats['rejected_unverifiable'] += 1
            continue
        # Springs are permitted. The inclusive 2 000 KZT floor applies
        # to every selected type of product.
        if offer.cost is None or offer.cost < Decimal('2000'):
            stats['rejected_unverifiable'] += 1
            continue
        sellers = row.get('offers') or row.get('sellers') or []
        if not isinstance(sellers, list):
            sellers = []
        # Missing seller prices/identities make minimum-price evidence unsafe.
        valid_sellers = bool(sellers) and all(
            isinstance(seller, dict)
            and _money(seller.get('price')) is not None
            and (seller.get('sellerId') or seller.get('sellerName') or seller.get('name'))
            for seller in sellers)
        priced = [(_money(seller['price']),
                   str(seller.get('sellerName') or seller.get('name') or
                       seller.get('sellerId'))[:180])
                  for seller in sellers] if valid_sellers else []
        # Top-level minPrice is an observation, not seller evidence. A LOWER
        # top-level minimum indicates incomplete offers, so refuse the card.
        if not priced or (_money(row.get('minPrice')) is not None
                          and _money(row.get('minPrice')) < min(p for p, _ in priced)):
            stats['rejected_unconfirmed_price'] += 1
            stats['recheck_missing_sellers'] += 1
            continue
        stats['recheck_price_cards'] += 1
        price, seller_name = min(priced, key=lambda value: value[0])
        product_id = str(row['id'])[:80]
        key = offer.pk
        if key not in grouped:
            grouped[key] = {'offer': offer, 'minimum': price, 'seller': seller_name,
                            'product_id': product_id, 'title': title[:500],
                            'url': str(row.get('url') or '')[:2048],
                            'card_ids': set()}
        if price < grouped[key]['minimum']:
            grouped[key].update(minimum=price, seller=seller_name,
                                product_id=product_id, title=title[:500],
                                url=str(row.get('url') or '')[:2048])
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
                card_ids=sorted(entry['card_ids']),
                identity_status='needs_review',
            )
            record, created = KaspiPriceAudit.objects.select_for_update().get_or_create(
                offer=offer, source_filename=source, defaults=defaults)
            if not created:
                previous = set(record.card_ids or [])
                combined = sorted(previous | entry['card_ids'])
                record.card_ids = combined
                # Freeze the commercial configuration of the first matching
                # batch; subsequent batches may only improve the minimum.
                if price < record.min_price:
                    record.min_price = price
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
                record.save()
            stats['recheck_offer_updates'] += 1
            if record.roi < Decimal('50'):
                stats['rejected_below_50'] += 1
    # Only the audit table is altered. No old product or purchase is changed.
    return stats
