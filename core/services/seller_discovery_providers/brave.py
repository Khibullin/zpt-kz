"""Brave web discovery for auto-parts shops.

Uses the existing BraveSearchClient. Queries are ordinary web queries, not the
Instagram-only queries of collect_instagram_seller_leads().
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from django.conf import settings
from django.utils import timezone

from core.models import SellerLeadSource
from core.services.seller_discovery_identity import normalize_instagram_identity, normalize_seller_phone
from core.services.seller_discovery_providers.base import (
    DiscoveryProviderConfigError,
    SellerDiscoveryHit,
    require_discovery_provider,
)
from core.services.seller_discovery_providers.catalog import resolve_city
from core.services.seller_lead_search import (
    BraveSearchClient,
    parse_instagram_profile_url,
)

PROVIDER_NAME = 'brave'
BRAVE_CONFIDENCE_WEBSITE = 55
BRAVE_CONFIDENCE_INSTAGRAM = 50

PHONE_IN_TEXT_RE = re.compile(
    r'(?:'
    r'\+7[\s\-(]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}'
    r'|8[\s\-(]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}'
    r'|7[\s\-(]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}'
    r'|\b7\d{10}\b'
    r')',
)
INSTAGRAM_IN_TEXT_RE = re.compile(
    r'(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9._]{1,30})',
    re.IGNORECASE,
)
TITLE_SPLIT_RE = re.compile(r'\s+[|–—]\s+|\s+-\s+')
TITLE_HANDLE_RE = re.compile(r'\s*\(@[A-Za-z0-9._]+\)\s*')

# Catalogs and maps are not the shop itself. 2GIS firms belong to the 2GIS provider.
SKIPPED_RESULT_SUFFIXES = (
    '2gis.kz',
    '2gis.com',
    '2gis.ru',
    'google.com',
    'google.kz',
    'yandex.ru',
    'yandex.kz',
    'youtube.com',
    'facebook.com',
    'vk.com',
    'ok.ru',
    'tiktok.com',
    'olx.kz',
    'kolesa.kz',
    'kaspi.kz',
    'satu.kz',
    'krisha.kz',
)


def build_brave_discovery_query(*, city: str, direction: str) -> str:
    resolved = resolve_city(city)
    term = ' '.join(str(direction or '').split())
    return f'{term} {resolved.name} Казахстан'


def parse_brave_web_result(
    row: dict,
    *,
    city: str,
    direction: str,
    observed_at=None,
) -> SellerDiscoveryHit | None:
    if not isinstance(row, dict):
        return None
    result_url = str(row.get('url') or '').strip()
    if not result_url or _host_is_skipped(result_url):
        return None

    resolved = resolve_city(city)
    title = str(row.get('title') or '').strip()
    description = str(row.get('description') or '').strip()
    observed = observed_at or timezone.now()
    profile = parse_instagram_profile_url(result_url)
    text = f'{title}\n{description}'
    phones = _phones_from_text(text)
    instagram_url = ''
    source_type = SellerLeadSource.SOURCE_BRAVE_SEARCH
    confidence = BRAVE_CONFIDENCE_WEBSITE
    website = ''

    if profile:
        source_type = SellerLeadSource.SOURCE_INSTAGRAM
        instagram_url = profile['profile_url']
        confidence = BRAVE_CONFIDENCE_INSTAGRAM
        name = _clean_title(title) or profile['username']
    else:
        mentioned = _instagram_from_text(text)
        if mentioned:
            instagram_url = f'https://www.instagram.com/{mentioned}/'
        website = result_url
        name = _clean_title(title)

    if not name:
        return None

    raw = {
        'title': title,
        'url': result_url,
        'description': description,
    }
    return SellerDiscoveryHit(
        provider=PROVIDER_NAME,
        source_type=source_type,
        external_id='',
        name=name[:255],
        city=resolved.name,
        phone=phones[0] if phones else '',
        phones=tuple(phones),
        website=website[:500],
        instagram_url=instagram_url[:500],
        category_text='',
        source_url=result_url[:500],
        search_query=build_brave_discovery_query(city=resolved.name, direction=direction)[:200],
        confidence=confidence,
        raw_data=raw,
        observed_at=observed,
    )


class BraveWebDiscoveryProvider:
    name = PROVIDER_NAME

    def __init__(self, client: BraveSearchClient | None = None):
        self.client = client

    def search(
        self,
        *,
        city: str,
        direction: str,
        limit: int | None = None,
        max_pages: int | None = None,
    ) -> list[SellerDiscoveryHit]:
        resolved = resolve_city(city)
        require_discovery_provider(PROVIDER_NAME)
        query = build_brave_discovery_query(city=resolved.name, direction=direction)
        client = self.client or _client_from_settings()
        count = 5 if limit is None else max(1, min(int(limit), 20))
        rows = client.search(query, count=count)
        hits: list[SellerDiscoveryHit] = []
        seen_urls: set[str] = set()
        for row in rows:
            hit = parse_brave_web_result(row, city=resolved.name, direction=direction)
            if hit is None or hit.source_url in seen_urls:
                continue
            seen_urls.add(hit.source_url)
            hits.append(hit)
        return hits


def _client_from_settings() -> BraveSearchClient:
    api_key = (getattr(settings, 'BRAVE_SEARCH_API_KEY', '') or '').strip()
    if not api_key:
        raise DiscoveryProviderConfigError(
            'BRAVE_SEARCH_API_KEY не задан. Укажите ключ в переменных окружения.',
        )
    return BraveSearchClient(api_key=api_key)


def _host_is_skipped(url: str) -> bool:
    host = (urlsplit(url).hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    return any(host == suffix or host.endswith('.' + suffix) for suffix in SKIPPED_RESULT_SUFFIXES)


def _clean_title(title: str) -> str:
    text = TITLE_SPLIT_RE.split(title, maxsplit=1)[0]
    text = TITLE_HANDLE_RE.sub(' ', text)
    return ' '.join(text.split()).strip()[:255]


def _phones_from_text(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in PHONE_IN_TEXT_RE.finditer(text or ''):
        normalized = normalize_seller_phone(match.group(0))
        if not normalized or normalized in seen:
            continue
        if len(normalized) != 11 or not normalized.startswith('7'):
            continue
        seen.add(normalized)
        found.append(normalized)
    return found


def _instagram_from_text(text: str) -> str:
    match = INSTAGRAM_IN_TEXT_RE.search(text or '')
    if not match:
        return ''
    return normalize_instagram_identity(match.group(1))
