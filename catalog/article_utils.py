"""Article normalization for seller product lookup."""

from __future__ import annotations

import re

from django.db.models import Q, Value
from django.db.models.functions import Replace, Upper

from core.services.home_parts_query import looks_like_exact_article

_NON_ALNUM = re.compile(r'[^A-Za-z0-9]+')
ARTICLE_STRIP_CHARS = ('-', ' ', '/', '.', '_')


def normalize_article(value: str | None) -> str:
    """Compact article key: letters and digits only, uppercase.

    Product.article is not globally unique — this helper is only for matching.
    """
    text = str(value or '').strip()
    if not text:
        return ''
    return _NON_ALNUM.sub('', text).upper()


def display_article(value: str | None) -> str:
    return str(value or '').strip()


def compact_article_expression(field_name: str = 'article'):
    """SQL form of normalize_article for the punctuation stored on Product.article."""
    expr = Upper(field_name)
    for char in ARTICLE_STRIP_CHARS:
        expr = Replace(expr, Value(char), Value(''))
    return expr


def filter_products_by_public_query(products, query):
    """Public catalog search.

    A query that looks like an OEM/SKU matches only the normalized article.
    Other queries keep the existing title/article/description/compatibility search.
    """
    text = str(query or '').strip()
    if not text:
        return products
    if looks_like_exact_article(text):
        compact = normalize_article(text)
        if not compact:
            return products.none()
        return products.exclude(article='').annotate(
            article_compact=compact_article_expression(),
        ).filter(article_compact=compact)
    return products.filter(
        Q(title__icontains=text)
        | Q(article__icontains=text)
        | Q(description__icontains=text)
        | Q(compatibility__icontains=text)
    )
