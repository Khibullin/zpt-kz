"""Select an observed price, without certifying identity or exhaustive search."""
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

INCOMPLETE_PRICE_SCOPE = (
    'Минимум среди полученных карточек; полнота поиска Kaspi не подтверждена.'
)


def _normalize(value):
    return re.sub(r'[^A-Z0-9]', '', str(value or '').upper())


def lowest_observed_quote(rows, *, brand, article, city_id='750000000', verified_product_id=None):
    """Prefer the lowest matching observation, never an old unverified URL.

    Exact brand/article matching makes a price candidate only. Side, execution,
    quantity, supplier stock and purchase eligibility remain separate checks.
    A previously verified identity can restrict the selection to its own card.
    """
    wanted = (_normalize(brand), _normalize(article))
    if not all(wanted):
        return None
    candidates = []
    for row in rows:
        if str(row.get('cityId')) != str(city_id):
            continue
        if _normalize(row.get('brand')) != wanted[0]:
            continue
        article_tokens = {_normalize(token) for token in re.findall(
            r'[A-Za-z0-9]+(?:[-./][A-Za-z0-9]+)*', str(row.get('title') or ''))}
        if wanted[1] not in article_tokens:
            continue
        source = urlsplit(row.get('url') or '')
        product_id = str(row.get('id') or '')
        match = re.search(r'-(\d+)/?$', source.path)
        if source.scheme != 'https' or source.hostname != 'kaspi.kz' or not match or match.group(1) != product_id:
            continue
        if verified_product_id is not None and product_id != str(verified_product_id):
            continue
        if row.get('currency', 'KZT') != 'KZT' or not row.get('offers'):
            continue
        try:
            price = Decimal(str(row.get('minPrice')))
            prices = [Decimal(str(s.get('price'))) for s in row['offers']]
            if not price.is_finite() or price <= 0 or any(not p.is_finite() or p <= 0 for p in prices):
                continue
            if min(prices) != price:
                continue
        except (InvalidOperation, TypeError, ValueError):
            continue
        candidates.append((price, product_id, row))
    return min(candidates, key=lambda item: (item[0], item[1]))[2] if candidates else None
