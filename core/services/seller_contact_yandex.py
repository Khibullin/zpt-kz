"""Yandex Organization Search as a transient website locator.

The official Search API for organizations is called. Yandex Maps HTML is not
scraped. Organization name, address, phone, coordinates, and the Yandex object
id are used only in memory for matching, or dropped. They are not returned for
storage: the Organization Search license is not clear enough to keep a copy of
those fields. The only value passed onward is a website URL to fetch ourselves.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, parse, request

from django.conf import settings

from core.services.seller_contact_website import distinctive_name_tokens
from core.services.seller_discovery_identity import normalize_seller_name

logger = logging.getLogger(__name__)

YANDEX_SEARCH_URL = 'https://search-maps.yandex.ru/v1/'
MAX_RESULTS = 5
FETCH_TIMEOUT = 8.0
MATCH_THRESHOLD = 70
MATCH_GAP = 15


class YandexOrgError(Exception):
    """Organization Search failed. The message never contains the API key."""


@dataclass(frozen=True)
class YandexOrgLocator:
    """Transient match. website_uri must not be written as Yandex evidence."""

    website_uri: str = ''
    ambiguous: bool = False
    error: str = ''


def yandex_org_enabled() -> bool:
    return bool(getattr(settings, 'SELLER_CONTACT_ENRICHMENT_ENABLED', False)) and bool(
        getattr(settings, 'SELLER_CONTACT_YANDEX_ENABLED', False),
    )


def locate_yandex_organization(
    *,
    name: str,
    city: str = '',
    urlopen: Callable[..., Any] | None = None,
) -> YandexOrgLocator:
    """One Organization Search. Phones and the Yandex id are discarded."""
    if not yandex_org_enabled():
        raise YandexOrgError(
            'Yandex Organization Search выключен. Нужны SELLER_CONTACT_ENRICHMENT_ENABLED и '
            'SELLER_CONTACT_YANDEX_ENABLED.',
        )
    api_key = (getattr(settings, 'YANDEX_ORG_SEARCH_API_KEY', '') or '').strip()
    if not api_key:
        raise YandexOrgError('YANDEX_ORG_SEARCH_API_KEY не задан.')
    opener = urlopen or _urlopen_without_proxy
    query = ' '.join(part for part in (name, city) if part).strip()
    payload = _search(query, api_key=api_key, opener=opener)
    ranked = []
    for feature in _features(payload):
        score = _match_score(feature, lead_name=name, city=city)
        if score:
            ranked.append((score, feature))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked or ranked[0][0] < MATCH_THRESHOLD:
        return YandexOrgLocator(error='нет уверенного совпадения')
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < MATCH_GAP:
        return YandexOrgLocator(ambiguous=True, error='неоднозначное совпадение')
    return YandexOrgLocator(website_uri=_website(ranked[0][1]))


def _search(query: str, *, api_key: str, opener: Callable[..., Any]) -> dict[str, Any]:
    params = parse.urlencode({
        'apikey': api_key,
        'text': query,
        'lang': 'ru_RU',
        'type': 'biz',
        'results': str(MAX_RESULTS),
    })
    http_request = request.Request(
        f'{YANDEX_SEARCH_URL}?{params}',
        headers={'Accept': 'application/json'},
        method='GET',
    )
    logger.info('Yandex Organization Search query=%r', query)
    try:
        with opener(http_request, timeout=FETCH_TIMEOUT) as response:
            status = getattr(response, 'status', 200)
            raw = response.read()
    except error.HTTPError as exc:
        raw = exc.read() if exc.fp else b''
        _raise_http(exc.code, raw, api_key=api_key)
    except error.URLError as exc:
        reason = _redact(str(getattr(exc, 'reason', exc)), api_key=api_key)
        if 'timed out' in reason.lower():
            raise YandexOrgError('Yandex Organization Search timeout') from None
        raise YandexOrgError(f'Yandex Organization Search network error: {reason}') from None
    if status == 429:
        raise YandexOrgError('Yandex Organization Search HTTP 429') from None
    if status >= 400:
        _raise_http(status, raw, api_key=api_key)
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    try:
        payload = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        preview = _redact(raw.decode('utf-8', errors='replace')[:180], api_key=api_key)
        raise YandexOrgError(f'Yandex Organization Search вернул не JSON: {preview}') from None
    if not isinstance(payload, dict):
        raise YandexOrgError('Yandex Organization Search вернул неожиданный JSON')
    return payload


def _raise_http(status: int, raw: bytes, *, api_key: str) -> None:
    if status == 429:
        raise YandexOrgError('Yandex Organization Search HTTP 429')
    detail = _redact(raw.decode('utf-8', errors='replace')[:180], api_key=api_key)
    message = f'Yandex Organization Search HTTP {status}'
    if detail:
        message = f'{message}: {detail}'
    raise YandexOrgError(message)


def _features(payload: dict[str, Any]) -> list[dict[str, Any]]:
    features = payload.get('features') or []
    return [item for item in features if isinstance(item, dict)][:MAX_RESULTS]


def _company(feature: dict[str, Any]) -> dict[str, Any]:
    properties = feature.get('properties') if isinstance(feature.get('properties'), dict) else {}
    meta = properties.get('CompanyMetaData') if isinstance(properties.get('CompanyMetaData'), dict) else {}
    return meta


def _website(feature: dict[str, Any]) -> str:
    meta = _company(feature)
    return str(meta.get('url') or '').strip()


def _match_score(feature: dict[str, Any], *, lead_name: str, city: str) -> int:
    meta = _company(feature)
    properties = feature.get('properties') if isinstance(feature.get('properties'), dict) else {}
    org_name = normalize_seller_name(str(meta.get('name') or properties.get('name') or ''))
    tokens = distinctive_name_tokens(lead_name)
    matched = [token for token in tokens if token and token in org_name]
    if not (len(tokens) >= 2 and len(matched) >= 2):
        return 0
    address = str(meta.get('address') or properties.get('description') or '')
    city_score = 30 if city and city.casefold() in address.casefold() else 0
    return 50 + city_score


def _redact(text: str, *, api_key: str) -> str:
    redacted = str(text or '')
    if api_key:
        redacted = redacted.replace(api_key, '[REDACTED]')
    return re.sub(r'(apikey=)[^&\s]+', r'\1[REDACTED]', redacted, flags=re.IGNORECASE)


def _urlopen_without_proxy(http_request: request.Request, timeout: float):
    opener = request.build_opener(request.ProxyHandler({}))
    return opener.open(http_request, timeout=timeout)
