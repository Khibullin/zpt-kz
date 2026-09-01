"""Article normalization for seller product lookup."""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r'[^A-Za-z0-9]+')


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
