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
    'Steering elements', 'Anti Roll Bars', 'Brake pads', 'Ball joints',
})


def _sku(value):
    # Exact characters; never drop separators or leading zeroes.
    return str(value or '').strip().casefold()


def _title_tokens(title):
    return {_sku(token.strip('()[]{}.,;:')) for token in str(title or '').split()}


def _one_unit(title):
    """Reject multiple quantity markers or kits; title alone is not proof."""
    title = str(title or '')
    units = re.findall(r'(?<!\d)(\d+)\s*шт(?=\s|[.,;:)\]]|$)', title, re.I)
    if units != ['1']:
        return False
    if re.search(r'\b(?:комплект|набор|пара|упаковка)\b', title, re.I):
        return False
    return True


def _article_in_slug(row, article):
    """Conservative URL-SKU consistency check for analogue mentions."""
    slug = urlsplit(str(row.get('url') or '')).path.rstrip('/').rsplit('/', 1)[-1]
    slug = re.sub(r'-\d{6,15}$', '', slug)
    return bool(re.search(r'(?<![a-z0-9])' + re.escape(article) +
                          r'(?![a-z0-9])', slug, re.I))


def _brand_in_card_title_and_url(row, brand):
    """Reject a card whose title or product slug contradicts its metadata brand.

    Brand in Apify metadata alone is not an identity verification. Fail closed
    on brand aliases/transliterations needing independent checking.
    """
    title = str(row.get('title') or '').casefold()
    slug = urlsplit(str(row.get('url') or '')).path.rstrip('/').rsplit('/', 1)[-1].casefold()
    slug = re.sub(r'-\d{6,15}$', '', slug)
    title_pattern = re.escape(brand).replace(r'\ ', r'\s+')
    slug_pattern = re.escape(re.sub(r'\s+', '-', brand))
    return bool(re.search(r'(?<!\w)' + title_pattern + r'(?!\w)', title)
                and re.search(r'(?<![a-z0-9])' + slug_pattern
                              + r'(?![a-z0-9])', slug))


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
    equal_minimum_ids = defaultdict(set)
    # If ANY otherwise relevant live card has an inconsistent/partial seller
    # snapshot, a more expensive valid card cannot certify this SKU's minimum.
    # Fail closed for the WHOLE SKU, not just the malformed card.
    compromised_skus = set()
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
        # A product can mention the requested article exclusively as an
        # analogue, including in BOTH title and URL slug. The old slug
        # consistency check alone could treat that analogue as the main
        # item when the actual sold article was not among target_skus.
        # Without a structured primary SKU, fail closed for analogue/
        # replacement listings and send them to manual identity review.
        slug = urlsplit(str(row.get('url') or '')).path.rstrip('/').rsplit('/', 1)[-1]
        if (re.search(r'(?i)(?:\bаналог(?:и|ов)?\b|\bзамена\b|\banalog(?:ue|s)?\b|\breplacement\b)', title)
                or re.search(r'(?i)(?:^|-)(?:analog(?:ue|s)?|replacement|analogi?|zamena)(?:-|$)', slug)):
            continue
        articles = allowed.intersection(_title_tokens(title))
        # A title containing two target SKUs (e.g. an analogue list) proves
        # neither product's identity. Reject rather than double-linking.
        if len(articles) != 1:
            continue
        key = (brand, next(iter(articles)))
        # A title may list a target SKU only as an analogue, not the product.
        # Missing SKU in Kaspi slug needs manual review; no auto quote match.
        if not _article_in_slug(row, key[1]) or not _brand_in_card_title_and_url(row, brand):
            continue
        sellers = row.get('offers')
        if not isinstance(sellers, list) or not sellers:
            compromised_skus.add(key)
            continue
        # A partial sellers array cannot prove the lowest quoted price.
        # Do not silently treat a truncated list as a complete comparison.
        claimed_count = row.get('sellersCount')
        # Without a trustworthy count we cannot establish that *all* seller
        # offers were returned. A mismatch in EITHER direction is an anomaly.
        if (isinstance(claimed_count, bool) or claimed_count is None
                or not str(claimed_count).isdigit()
                or int(claimed_count) != len(sellers)):
            compromised_skus.add(key)
            continue
        # A broken or unidentified seller offer makes the claimed minimum
        # incomplete. Do not silently discard it and accept a higher quote.
        if any(not isinstance(s, dict) or _price(s.get('price')) is None
               or not (s.get('sellerId') or s.get('sellerName'))
               for s in sellers):
            compromised_skus.add(key)
            continue
        priced = [(_price(s['price']), s) for s in sellers]
        price, seller = min(priced, key=lambda entry: entry[0])
        # A disagreeing card-level minPrice is an anomaly, not seller evidence.
        if _price(row.get('minPrice')) != price:
            compromised_skus.add(key)
            continue
        for article in articles:
            key = (brand, article)
            observed_ids[key].add(str(row['id']))
            prev = chosen.get(key)
            if prev is None or price < prev['min_price']:
                equal_minimum_ids[key] = {str(row['id'])}
            elif price == prev['min_price']:
                equal_minimum_ids[key].add(str(row['id']))
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
    # Do not substitute a higher, well-formed quote when a second matching
    # Kaspi card was observed but its prices could not be reconciled.
    for key in compromised_skus:
        chosen.pop(key, None)
    for key, quote in chosen.items():
        quote['observed_card_ids'] = sorted(observed_ids[key])
        quote['equal_minimum_card_ids'] = sorted(equal_minimum_ids[key])
        # Equal quote does not establish equal execution or photo identity.
        quote['needs_identity_choice'] = len(equal_minimum_ids[key]) > 1
    return chosen
