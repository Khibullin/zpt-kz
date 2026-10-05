"""Decide whether a SellerLead belongs to the Kazakhstan market.

Only the lead's own city, saved locations, and its own website domain count.
A foreign city mentioned in passing does not make the lead foreign.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from core.models import (
    MARKET_SCOPE_FOREIGN,
    MARKET_SCOPE_KZ,
    MARKET_SCOPE_UNKNOWN,
    SellerLead,
)
from core.services.seller_discovery_providers.catalog import KZ_DISCOVERY_CITY_NAMES

FOREIGN_CITIES = (
    'минск',
    'бишкек',
    'москва',
    'санкт-петербург',
    'киев',
    'ташкент',
    'новосибирск',
    'екатеринбург',
    'омск',
    'краснодар',
)
FOREIGN_COUNTRY_MARKERS = (
    'беларус',
    'кыргыз',
    'россия',
    'узбекистан',
    'украина',
)
FOREIGN_TLDS = frozenset({'by', 'ru', 'kg', 'uz', 'ua'})


def _fold(value: str) -> str:
    return ' '.join(str(value or '').casefold().replace('ё', 'е').split())


def _contains_term(text: str, term: str) -> bool:
    folded = _fold(text)
    needle = _fold(term)
    if not folded or not needle:
        return False
    return re.search(r'(?<![\w])' + re.escape(needle) + r'(?![\w])', folded) is not None


def _matches_city(text: str, cities: tuple[str, ...]) -> str:
    for city in cities:
        if _contains_term(text, city):
            return city
    return ''


def _website_tld(lead: SellerLead) -> str:
    host = (lead.normalized_domain or '').strip().lower()
    if not host and lead.website_url:
        host = (urlsplit(lead.website_url).hostname or '').lower()
    host = host[4:] if host.startswith('www.') else host
    if '.' not in host:
        return ''
    return host.rsplit('.', 1)[-1]


def qualify_market(lead: SellerLead) -> tuple[str, str]:
    """Return market scope and a short reason. Does not write."""
    primary = str(lead.city or '')
    kz_city = _matches_city(primary, KZ_DISCOVERY_CITY_NAMES)
    if kz_city:
        return MARKET_SCOPE_KZ, f'city={lead.city}'
    foreign_city = _matches_city(primary, FOREIGN_CITIES)
    foreign_country = next((marker for marker in FOREIGN_COUNTRY_MARKERS if _contains_term(primary, marker)), '')
    if foreign_city or foreign_country:
        return MARKET_SCOPE_FOREIGN, f'city={lead.city}'

    for location in lead.locations.all():
        city = str(location.city or '')
        kz_city = _matches_city(city, KZ_DISCOVERY_CITY_NAMES)
        if kz_city:
            return MARKET_SCOPE_KZ, f'location={location.city}'
        foreign_city = _matches_city(city, FOREIGN_CITIES)
        foreign_country = next((marker for marker in FOREIGN_COUNTRY_MARKERS if _contains_term(city, marker)), '')
        if foreign_city or foreign_country:
            return MARKET_SCOPE_FOREIGN, f'location={location.city}'

    tld = _website_tld(lead)
    if tld == 'kz':
        return MARKET_SCOPE_KZ, 'domain=.kz'
    if tld in FOREIGN_TLDS:
        return MARKET_SCOPE_FOREIGN, f'domain=.{tld}'
    return MARKET_SCOPE_UNKNOWN, ''
