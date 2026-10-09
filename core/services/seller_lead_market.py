"""Decide whether a SellerLead belongs to the Kazakhstan market.

A discovery city such as Алматы does not override the business itself.
Two agreeing signals from the name, username, profile, saved location, or
official domain make the lead foreign. One such signal against a Kazakhstan
city is a conflict and stays unknown. A foreign city only in search evidence
does not count.
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
    'minsk',
    'бишкек',
    'bishkek',
    'москва',
    'moscow',
    'санкт-петербург',
    'киев',
    'kyiv',
    'kiev',
    'ташкент',
    'tashkent',
    'новосибирск',
    'екатеринбург',
    'омск',
    'краснодар',
)
FOREIGN_COUNTRY_MARKERS = (
    'беларус',
    'кыргыз',
    'россия',
    'российская федерация',
    'рф',
    'russia',
    'узбекистан',
    'украина',
)
FOREIGN_TLDS = frozenset({'by', 'ru', 'xn--p1ai', 'kg', 'uz', 'ua'})


def _fold(value: str) -> str:
    return ' '.join(str(value or '').casefold().replace('ё', 'е').split())


def _contains_term(text: str, term: str) -> bool:
    folded = _fold(text)
    needle = _fold(term)
    if not folded or not needle:
        return False
    return re.search(r'(?<![\w])' + re.escape(needle) + r'(?![\w])', folded) is not None


def _matches_city(text: str, cities: tuple[str, ...]) -> str:
    folded = _fold(text)
    for city in cities:
        needle = _fold(city)
        if not folded or not needle:
            continue
        match = re.search(r'(?<![\w])' + re.escape(needle) + r'(?![\w])', folded)
        if not match:
            continue
        if re.search(r'(?<![\w])из\s+$', folded[:match.start()]):
            continue
        return city
    return ''


def _business_foreign_signals(lead: SellerLead) -> list[str]:
    """Signals that describe the business, not the discovery query or a search hit."""
    signals = []
    for label, value in (
        ('name', lead.name),
        ('instagram', lead.instagram_username),
        ('profile', lead.profile_description),
    ):
        text = str(value or '')
        city = _matches_city(text, FOREIGN_CITIES)
        if city:
            signals.append(f'{label}={city}')
        country = next(
            (marker for marker in FOREIGN_COUNTRY_MARKERS if _contains_term(text, marker)),
            '',
        )
        if country:
            signals.append(f'{label}_country={country}')
    tld = _website_tld(lead)
    if tld in FOREIGN_TLDS:
        signals.append(f'domain=.{tld}')
    whatsapp = ''.join(ch for ch in str(lead.whatsapp or '') if ch.isdigit())
    if len(whatsapp) == 11 and whatsapp.startswith('79'):
        signals.append('phone=+7-9xx')
    for location in lead.locations.all():
        city = _matches_city(str(location.city or ''), FOREIGN_CITIES)
        if city:
            signals.append(f'location={city}')
            break
    return signals


def _website_tld(lead: SellerLead) -> str:
    host = (lead.normalized_domain or '').strip().lower()
    if not host and lead.website_url:
        host = (urlsplit(lead.website_url).hostname or '').lower()
    host = host[4:] if host.startswith('www.') else host
    if '.' not in host:
        return ''
    return host.rsplit('.', 1)[-1]


def _market_from_signals(signals: list[str], *, kz_city: str) -> tuple[str, str] | None:
    if kz_city and len(signals) >= 2:
        return MARKET_SCOPE_FOREIGN, 'identity=' + ','.join(signals)
    if kz_city and len(signals) == 1:
        return MARKET_SCOPE_UNKNOWN, f'conflict city={kz_city} {signals[0]}'
    if not kz_city and signals:
        return MARKET_SCOPE_FOREIGN, 'identity=' + ','.join(signals)
    return None


def qualify_market(lead: SellerLead) -> tuple[str, str]:
    """Return market scope and a short reason. Does not write."""
    primary = str(lead.city or '')
    kz_city = _matches_city(primary, KZ_DISCOVERY_CITY_NAMES)
    foreign_city = _matches_city(primary, FOREIGN_CITIES)
    foreign_country = next((marker for marker in FOREIGN_COUNTRY_MARKERS if _contains_term(primary, marker)), '')
    if foreign_city or foreign_country:
        return MARKET_SCOPE_FOREIGN, f'city={lead.city}'
    identity = _business_foreign_signals(lead)
    resolved = _market_from_signals(identity, kz_city=primary if kz_city else '')
    if resolved is not None:
        return resolved
    if kz_city:
        return MARKET_SCOPE_KZ, f'city={lead.city}'

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
