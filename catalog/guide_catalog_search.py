"""Public catalog lookup for the ZPT Гид assistant.

Returns only published products and public fields. Does not expose cost,
seller internals, warehouse codes, or PP1/PP2 balances.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db.models import Value
from django.db.models.functions import Replace, Upper

from catalog.article_utils import normalize_article
from catalog.home_parts_search import _ARTICLE_STRIP_CHARS
from catalog.models import Product
from catalog.templatetags.product_extras import public_product_url
from catalog.wholesale import public_stock_status

logger = logging.getLogger(__name__)

MAX_RESULTS = 5
STOCK_UNKNOWN_LABEL = 'Уточните наличие у продавца'
SEARCH_UNAVAILABLE = 'Сейчас не удалось проверить каталог.'


def _public_products():
    return Product.objects.filter(status='active').only(
        'id',
        'title',
        'article',
        'slug',
        'price',
        'price_on_request',
        'stock_qty',
        'status',
    )


def _compact_article_expr():
    expr = Upper('article')
    for char in _ARTICLE_STRIP_CHARS:
        expr = Replace(expr, Value(char), Value(''))
    return expr


def _absolute_product_url(product) -> str:
    path = public_product_url(product)
    base = str(getattr(settings, 'PUBLIC_BASE_URL', '') or 'https://zpt.kz').rstrip('/')
    if path.startswith('http://') or path.startswith('https://'):
        return path
    return f'{base}{path}'


def _availability_label(product) -> str:
    status = public_stock_status(product)
    code = str(status.get('code') or '')
    if code == 'unknown' or status.get('qty') is None:
        return STOCK_UNKNOWN_LABEL
    if code == 'out':
        return str(status.get('label') or 'Нет в наличии')
    return str(status.get('label') or 'В наличии')


def _public_price(product):
    if bool(getattr(product, 'price_on_request', False)):
        return None
    price = getattr(product, 'price', None)
    if price is None:
        return None
    return int(price)


def serialize_public_product(product, *, match_kind: str) -> dict:
    return {
        'title': str(getattr(product, 'title', '') or ''),
        'article': str(getattr(product, 'article', '') or ''),
        'price': _public_price(product),
        'price_on_request': bool(getattr(product, 'price_on_request', False)),
        'availability': _availability_label(product),
        'url': _absolute_product_url(product),
        'match_kind': match_kind,
    }


def _rank_products(query: str) -> list[tuple[object, str]]:
    qs = _public_products()
    compact = normalize_article(query)
    ranked: list[tuple[object, str]] = []
    found_ids: set[int] = set()

    if compact:
        exact = qs.exclude(article='').annotate(
            article_compact=_compact_article_expr(),
        ).filter(article_compact=compact)[:MAX_RESULTS]
        for product in exact:
            if product.id in found_ids:
                continue
            found_ids.add(product.id)
            ranked.append((product, 'article_exact'))
            if len(ranked) >= MAX_RESULTS:
                return ranked

    if len(ranked) < MAX_RESULTS:
        article_hits = qs.filter(article__icontains=query).exclude(pk__in=found_ids)
        for product in article_hits[:MAX_RESULTS]:
            if product.id in found_ids:
                continue
            found_ids.add(product.id)
            ranked.append((product, 'article'))
            if len(ranked) >= MAX_RESULTS:
                return ranked

    if len(ranked) < MAX_RESULTS:
        title_hits = qs.filter(title__icontains=query).exclude(pk__in=found_ids)
        for product in title_hits[:MAX_RESULTS]:
            if product.id in found_ids:
                continue
            found_ids.add(product.id)
            ranked.append((product, 'title'))
            if len(ranked) >= MAX_RESULTS:
                break
    return ranked


def search_public_catalog(query: str) -> dict:
    """Search published catalog products by article or name.

    Exact compact-article matches come first. Leading zeros are kept.
    """
    text = ' '.join(str(query or '').split())
    if not text:
        return {
            'ok': True,
            'query': '',
            'results': [],
            'not_found': True,
        }
    try:
        ranked = _rank_products(text)
        results = [
            serialize_public_product(product, match_kind=kind)
            for product, kind in ranked
        ]
    except Exception:
        logger.warning('ZPT Guide catalog search failed')
        return {
            'ok': False,
            'error': 'unavailable',
            'message': SEARCH_UNAVAILABLE,
        }
    payload = {
        'ok': True,
        'query': text,
        'results': results,
        'not_found': not results,
    }
    if not results:
        payload['message'] = 'В каталоге ZPT не найден'
    return payload
