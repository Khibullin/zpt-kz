"""Read-only, fail-closed minimum Kaspi quote selection for staging.

Do not publish or modify Offer, audit history, stock, prices, or purchases here.
A verified *seller quote* is not verification of part execution or fitment.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
import re

SUPPORTED_CATEGORIES = frozenset({
    'Shock absorbers', 'Suspension spring', 'Drive elements', 'Brake discs',
    'Suspension arms and rods', 'Shock absorber support',
    'Steering elements', 'Anti Roll Bars',
})


def _sku(value):
    # Exact characters; never drop separators or leading zeroes.
    return str(value or '').strip().casefold()


def _title_tokens(title):
    return {_sku(token.strip('()[]{}.,;:')) for token in str(title or '').split()}


def _one_unit(title):
    return bool(re.search(r'(?<!\d)1\s*шт(?=\s|\.|$)', str(title or ''), re.I))


def _price(value):
    try:
        result = Decimal(str(value))
        if result.is_finite() and result > 0:
            return result
    except (ValueError, InvalidOperation, TypeError):
        pass
    return None


def _valid_url(row):
    url = str(row.get('url') or '')
    parsed = urlsplit(url)
    match = re.search(r'-(\d{6,15})/?$', parsed.path)
    return bool(parsed.scheme == 'https' and parsed.hostname == 'kaspi.kz'
                and parsed.path.startswith('/shop/p/') and match
                and match.group(1) == str(row.get('id') or ''))


def select_minimum_seller_quotes(rows, target_skus, *, allowed_categories=SUPPORTED_CATEGORIES):
    """Match cards against *all* supplier SKUs, not row.searchQuery.

    For each exact case-insensitive brand+full article, collect matching 1-piece
    Kaspi cards. Keep the lowest *observed* real seller offer, not a top-level
    price nor an unrelated search result. This is not a nationwide-minimum
    certification or a sale-ready identity attestation.

    'rows' can be an iterator over consecutive batches. Return dictionaries
    suitable for staging display; call downstream identity/ROI gates before
    any publication. Unknown or untrusted rows are skipped.
    """
    by_brand = defaultdict(set)
    for brand, article in target_skus:
        brand, article = _sku(brand), _sku(article)
        if brand and article:
            by_brand[brand].add(article)

    chosen = {}
    observed_ids = defaultdict(set)
    for row in rows:
        if not isinstance(row, dict):
            continue
        brand = _sku(row.get('brand'))
        allowed = by_brand.get(brand, set())
        if not allowed or not _valid_url(row) or row.get('inStock') is not True:
            continue
        if row.get('currency') != 'KZT' or row.get('categoryCode') not in allowed_categories:
            continue
        title = str(row.get('title') or '')
        if not _one_unit(title):
            continue
        articles = allowed.intersection(_title_tokens(title))
        if not articles:
            continue
        sellers = row.get('offers')
        if not isinstance(sellers, list) or not sellers:
            continue
        priced = [(_price(s.get('price')), s) for s in sellers if isinstance(s, dict)]
        priced = [(amount, seller) for amount, seller in priced if amount is not None]
        if not priced:
            continue
        price, seller = min(priced, key=lambda entry: entry[0])
        # A disagreeing card-level minPrice is an anomaly, not seller evidence.
        if _price(row.get('minPrice')) != price:
            continue
        for article in articles:
            key = (brand, article)
            observed_ids[key].add(str(row['id']))
            prev = chosen.get(key)
            if prev is None or (price, str(row['id'])) < (prev['min_price'], prev['product_id']):
                chosen[key] = dict(
                    brand=brand, article=article, min_price=price,
                    product_id=str(row['id']), card_url=row['url'],
                    card_title=title, seller_id=str(seller.get('sellerId') or ''),
                    seller_name=str(seller.get('sellerName') or ''),
                    source_query=str(row.get('searchQuery') or ''),
                    city=str(row.get('city') or ''),
                    publication_verified=False,
                )
    for key, quote in chosen.items():
        quote['observed_card_ids'] = sorted(observed_ids[key])
    return chosen
