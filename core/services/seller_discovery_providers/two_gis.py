"""2GIS Places API client for Seller Discovery.

Reads organizations and returns SellerDiscoveryHit values. The API key is never
written into hits, logs, or exception text.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable
from urllib import error, parse, request

from django.conf import settings
from django.utils import timezone

from core.models import SellerLeadSource
from core.services.seller_discovery_identity import normalize_instagram_identity, normalize_seller_phone
from core.services.seller_discovery_providers.base import (
    DiscoveryProviderConfigError,
    DiscoveryProviderError,
    SellerDiscoveryHit,
    require_discovery_provider,
)
from core.services.seller_discovery_providers.catalog import (
    TWO_GIS_MAX_PAGES_CAP,
    TWO_GIS_MAX_PAGES_DEFAULT,
    TWO_GIS_PAGE_SIZE_CAP,
    TWO_GIS_PAGE_SIZE_DEFAULT,
    DiscoveryCity,
    bounded_search_radius,
    resolve_city,
)

logger = logging.getLogger(__name__)

PROVIDER_NAME = 'two_gis'
DEFAULT_CATALOG_URL = 'https://catalog.api.2gis.com/3.0/items'
DEFAULT_TIMEOUT = 10.0
TWO_GIS_CONFIDENCE = 80
ITEM_FIELDS = ','.join((
    'items.point',
    'items.full_address_name',
    'items.rubrics',
    'items.org',
    'items.brand',
    'items.contact_groups',
    'items.adm_div',
))
# Contact types documented by the 2GIS Catalog API. items.links is a list of
# related catalog objects, not the business website or Instagram.
CONTACT_TYPE_WEBSITE = 'website'
CONTACT_TYPE_INSTAGRAM = 'instagram'
_COORD_QUANT = Decimal('0.000001')


class TwoGisPlacesClient:
    """HTTP client for catalog.api.2gis.com/3.0/items.

    Pagination is bounded by page_size and max_pages. An empty page or a short
    page ends the loop. HTTP 429 is raised once and is not retried.
    """

    def __init__(
        self,
        api_key: str,
        *,
        catalog_url: str = DEFAULT_CATALOG_URL,
        timeout: float = DEFAULT_TIMEOUT,
        urlopen: Callable[..., Any] | None = None,
    ):
        self.api_key = _sanitize_api_key(api_key)
        self.catalog_url = (catalog_url or DEFAULT_CATALOG_URL).strip()
        self.timeout = timeout
        self._urlopen = urlopen or _urlopen_without_proxy

    def search_items(
        self,
        query: str,
        *,
        city: DiscoveryCity,
        limit: int | None = None,
        page_size: int | None = None,
        max_pages: int | None = None,
    ) -> list[dict[str, Any]]:
        bounded_search_radius(city.radius_m)
        size = _bounded_page_size(page_size if page_size is not None else limit)
        pages = _bounded_max_pages(max_pages)
        collected: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for page in range(1, pages + 1):
            batch = self._fetch_page(query, city=city, page=page, page_size=size)
            if not batch:
                break
            for item in batch:
                item_id = str(item.get('id') or '')
                if item_id and item_id in seen_ids:
                    continue
                if item_id:
                    seen_ids.add(item_id)
                collected.append(item)
            if len(batch) < size:
                break
        return collected

    def _fetch_page(
        self,
        query: str,
        *,
        city: DiscoveryCity,
        page: int,
        page_size: int,
    ) -> list[dict[str, Any]]:
        params = {
            'q': ' '.join(str(query or '').split()),
            'type': 'branch',
            'point': f'{city.longitude:.6f},{city.latitude:.6f}',
            'radius': str(city.radius_m),
            'page': str(page),
            'page_size': str(page_size),
            'locale': 'ru_KZ',
            'fields': ITEM_FIELDS,
            'key': self.api_key,
        }
        request_url = f'{self.catalog_url}?{parse.urlencode(params)}'
        http_request = request.Request(
            request_url,
            headers={'Accept': 'application/json'},
            method='GET',
        )
        logger.info(
            '2GIS search city=%s query=%r page=%s page_size=%s',
            city.name,
            params['q'],
            page,
            page_size,
        )
        try:
            with self._urlopen(http_request, timeout=self.timeout) as response:
                raw_body = response.read()
                status_code = getattr(response, 'status', 200)
                headers = getattr(response, 'headers', None)
        except error.HTTPError as exc:
            raw_body = exc.read() if exc.fp else b''
            self._raise_http(exc.code, raw_body, getattr(exc, 'headers', None))
        except error.URLError as exc:
            reason = _redact(str(getattr(exc, 'reason', exc)), api_key=self.api_key)
            if 'timed out' in reason.lower():
                raise DiscoveryProviderError('2GIS Places API timeout') from exc
            raise DiscoveryProviderError(f'2GIS Places API network error: {reason}') from exc

        if status_code >= 400:
            self._raise_http(status_code, raw_body, headers)
        payload = _parse_json(raw_body, headers, api_key=self.api_key, status_code=status_code)
        meta = payload.get('meta') if isinstance(payload.get('meta'), dict) else {}
        meta_code = meta.get('code')
        if isinstance(meta_code, int) and meta_code >= 400:
            self._raise_http(meta_code, raw_body, headers)
        result = payload.get('result') if isinstance(payload.get('result'), dict) else {}
        items = result.get('items') or []
        return [item for item in items if isinstance(item, dict)]

    def _raise_http(self, status_code: int, raw_body: bytes, headers: Any) -> None:
        detail = _error_detail(raw_body, headers, api_key=self.api_key)
        message = f'2GIS Places API HTTP {status_code}'
        if detail:
            message = f'{message}: {detail}'
        raise DiscoveryProviderError(message)


class TwoGisDiscoveryProvider:
    name = PROVIDER_NAME

    def __init__(self, client: TwoGisPlacesClient | None = None):
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
        client = self.client or _client_from_settings()
        items = client.search_items(
            direction,
            city=resolved,
            page_size=limit,
            max_pages=max_pages,
        )
        hits: list[SellerDiscoveryHit] = []
        seen_ids: set[str] = set()
        for item in items:
            hit = parse_two_gis_item(item, city=resolved, direction=direction)
            if hit is None or hit.external_id in seen_ids:
                continue
            seen_ids.add(hit.external_id)
            hits.append(hit)
        return hits


def parse_two_gis_item(
    item: dict[str, Any],
    *,
    city: DiscoveryCity | str,
    direction: str,
    observed_at=None,
) -> SellerDiscoveryHit | None:
    if not isinstance(item, dict):
        return None
    resolved = city if isinstance(city, DiscoveryCity) else resolve_city(city)
    item_type = str(item.get('type') or '').strip().lower()
    if item_type and item_type not in {'branch', 'organization'}:
        return None
    if not _item_matches_city(item, resolved):
        return None

    external_id = str(item.get('id') or '').strip()[:255]
    name = ' '.join(str(item.get('name') or '').split()).strip()[:255]
    if not external_id or not name:
        return None

    address = _address_from_item(item)
    latitude, longitude = _point_from_item(item)
    phones, whatsapp_phone = _phones_from_item(item)
    website, instagram_url = _website_and_instagram_from_contacts(item)
    rubrics = _rubrics_from_item(item)
    org = item.get('org') if isinstance(item.get('org'), dict) else {}
    org_id = str(org.get('id') or '').strip()[:64]
    brand_name = _brand_from_item(item)
    observed = observed_at or timezone.now()
    raw = _public_item(item)
    return SellerDiscoveryHit(
        provider=PROVIDER_NAME,
        source_type=SellerLeadSource.SOURCE_TWO_GIS,
        external_id=external_id,
        name=name,
        city=resolved.name,
        address=address[:500],
        latitude=latitude,
        longitude=longitude,
        phone=phones[0] if phones else '',
        phones=tuple(phones),
        whatsapp_phone=whatsapp_phone,
        website=website[:500],
        instagram_url=instagram_url[:500],
        category_text=(rubrics[0] if rubrics else '')[:100],
        rubrics=tuple(rubrics),
        source_url='',
        search_query=' '.join(str(direction or '').split())[:200],
        org_id=org_id,
        brand_name=brand_name[:100],
        confidence=TWO_GIS_CONFIDENCE,
        raw_data=raw,
        observed_at=observed,
    )


def _client_from_settings() -> TwoGisPlacesClient:
    api_key = (getattr(settings, 'TWO_GIS_API_KEY', '') or '').strip()
    if not api_key:
        raise DiscoveryProviderConfigError(
            'TWO_GIS_API_KEY не задан. Укажите ключ в переменных окружения.',
        )
    catalog_url = (getattr(settings, 'TWO_GIS_CATALOG_API_URL', '') or DEFAULT_CATALOG_URL).strip()
    return TwoGisPlacesClient(api_key=api_key, catalog_url=catalog_url)


def _sanitize_api_key(api_key: str) -> str:
    cleaned = str(api_key or '').strip().lstrip('\ufeff').strip('\r\n')
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in '"\'':
        cleaned = cleaned[1:-1].strip()
    if not cleaned:
        raise DiscoveryProviderConfigError(
            'TWO_GIS_API_KEY не задан. Укажите ключ в переменных окружения.',
        )
    if not cleaned.isascii() or any(character in cleaned for character in ' \t\n\r'):
        raise DiscoveryProviderConfigError(
            'TWO_GIS_API_KEY содержит недопустимые символы.',
        )
    return cleaned


def _urlopen_without_proxy(http_request: request.Request, timeout: float):
    opener = request.build_opener(request.ProxyHandler({}))
    return opener.open(http_request, timeout=timeout)


def _redact(text: str, *, api_key: str) -> str:
    redacted = str(text or '')
    if api_key:
        redacted = redacted.replace(api_key, '[REDACTED]')
        redacted = redacted.replace(parse.quote(api_key, safe=''), '[REDACTED]')
    redacted = re.sub(r'(key=)[^&\s]+', r'\1[REDACTED]', redacted, flags=re.IGNORECASE)
    return redacted


def _header(headers: Any, name: str) -> str:
    if headers is None:
        return ''
    value = headers.get(name) if hasattr(headers, 'get') else ''
    return str(value or '').split(';', 1)[0].strip()


def _decode_body(raw_body: bytes, headers: Any) -> bytes:
    encoding = _header(headers, 'Content-Encoding').lower()
    if encoding == 'gzip' or (len(raw_body) >= 2 and raw_body[:2] == b'\x1f\x8b'):
        try:
            return gzip.decompress(raw_body)
        except OSError:
            return raw_body
    return raw_body


def _parse_json(raw_body: bytes, headers: Any, *, api_key: str, status_code: int) -> dict[str, Any]:
    decoded = _decode_body(raw_body, headers)
    try:
        payload = json.loads(decoded.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        preview = _redact(decoded.decode('utf-8', errors='replace')[:200], api_key=api_key)
        raise DiscoveryProviderError(
            f'2GIS Places API returned invalid JSON: status={status_code}, body_preview={preview!r}',
        ) from exc
    if not isinstance(payload, dict):
        raise DiscoveryProviderError(f'2GIS Places API returned unexpected JSON: status={status_code}')
    return payload


def _error_detail(raw_body: bytes, headers: Any, *, api_key: str) -> str:
    decoded = _decode_body(raw_body, headers)
    if not decoded:
        return ''
    try:
        payload = json.loads(decoded.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _redact(decoded.decode('utf-8', errors='replace')[:200], api_key=api_key)
    if isinstance(payload, dict):
        meta = payload.get('meta') if isinstance(payload.get('meta'), dict) else {}
        for key in ('error', 'message'):
            if meta.get(key):
                return _redact(str(meta[key].get('message') if isinstance(meta[key], dict) else meta[key]), api_key=api_key)
        for key in ('message', 'error'):
            if payload.get(key):
                return _redact(str(payload[key]), api_key=api_key)
    return ''


def _item_matches_city(item: dict[str, Any], city: DiscoveryCity) -> bool:
    names: list[str] = []
    for division in item.get('adm_div') or []:
        if not isinstance(division, dict):
            continue
        if str(division.get('type') or '').strip().lower() != 'city':
            continue
        label = str(division.get('name') or '').strip()
        if label:
            names.append(label.casefold())
    if not names:
        return True
    allowed = {city.name.casefold(), *city.aliases}
    return bool(set(names) & allowed)


def _address_from_item(item: dict[str, Any]) -> str:
    full_address = ' '.join(str(item.get('full_address_name') or '').split())
    if full_address:
        return full_address
    return ' '.join(str(item.get('address_name') or '').split())


def _point_from_item(item: dict[str, Any]) -> tuple[Decimal | None, Decimal | None]:
    point = item.get('point') if isinstance(item.get('point'), dict) else {}
    return _coordinate(point.get('lat'), minimum=-90, maximum=90), _coordinate(
        point.get('lon'),
        minimum=-180,
        maximum=180,
    )


def _coordinate(value: Any, *, minimum: int, maximum: int) -> Decimal | None:
    if value is None or value == '':
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if number < minimum or number > maximum:
        return None
    return number.quantize(_COORD_QUANT, rounding=ROUND_HALF_UP)


def _phones_from_item(item: dict[str, Any]) -> tuple[list[str], str]:
    """Ordinary phone contacts stay phones. Only type=whatsapp becomes WhatsApp."""
    phones: list[str] = []
    seen: set[str] = set()
    whatsapp_phone = ''
    for contact in _iter_contacts(item):
        contact_type = str(contact.get('type') or '').strip().lower()
        if contact_type not in {'phone', 'whatsapp'}:
            continue
        raw = contact.get('value') or contact.get('text') or contact.get('print_text') or ''
        normalized = normalize_seller_phone(str(raw))
        if not normalized or len(normalized) != 11 or not normalized.startswith('7'):
            continue
        if contact_type == 'whatsapp':
            if not whatsapp_phone:
                whatsapp_phone = normalized
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        phones.append(normalized)
    return phones, whatsapp_phone


def _brand_from_item(item: dict[str, Any]) -> str:
    brand = item.get('brand')
    if isinstance(brand, dict):
        return ' '.join(str(brand.get('name') or '').split())
    return ''


def _bounded_page_size(value: int | None) -> int:
    if value is None:
        raw = getattr(settings, 'SELLER_DISCOVERY_2GIS_PAGE_SIZE', TWO_GIS_PAGE_SIZE_DEFAULT)
    else:
        raw = value
    try:
        number = int(raw)
    except (TypeError, ValueError):
        number = TWO_GIS_PAGE_SIZE_DEFAULT
    if number < 1:
        number = TWO_GIS_PAGE_SIZE_DEFAULT
    return min(number, TWO_GIS_PAGE_SIZE_CAP)


def _bounded_max_pages(value: int | None) -> int:
    if value is None:
        raw = getattr(settings, 'SELLER_DISCOVERY_2GIS_MAX_PAGES', TWO_GIS_MAX_PAGES_DEFAULT)
    else:
        raw = value
    try:
        number = int(raw)
    except (TypeError, ValueError):
        number = TWO_GIS_MAX_PAGES_DEFAULT
    if number < 1:
        number = TWO_GIS_MAX_PAGES_DEFAULT
    return min(number, TWO_GIS_MAX_PAGES_CAP)


def _website_and_instagram_from_contacts(item: dict[str, Any]) -> tuple[str, str]:
    """Read website and Instagram only from contact_groups entries.

    items.links lists related catalog objects and is ignored. A missing
    contact_groups field is an empty contact list, not an error.
    """
    website = ''
    instagram_url = ''
    for contact in _iter_contacts(item):
        contact_type = str(contact.get('type') or '').strip().lower()
        raw = contact.get('url') or contact.get('value') or contact.get('text') or ''
        if contact_type == CONTACT_TYPE_WEBSITE and not website:
            website = _http_url(raw)
        elif contact_type == CONTACT_TYPE_INSTAGRAM and not instagram_url:
            instagram_url = _instagram_url(raw)
    return website, instagram_url


def _rubrics_from_item(item: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for rubric in item.get('rubrics') or []:
        if not isinstance(rubric, dict):
            continue
        label = ' '.join(str(rubric.get('name') or '').split())
        if label and label not in names:
            names.append(label[:100])
    return names[:8]


def _iter_contacts(item: dict[str, Any]):
    for group in item.get('contact_groups') or []:
        if not isinstance(group, dict):
            continue
        for contact in group.get('contacts') or []:
            if isinstance(contact, dict):
                yield contact


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    """Drop anything that could carry the request key. The item itself should not."""
    safe = dict(item)
    safe.pop('key', None)
    return safe


def _http_url(value: Any) -> str:
    raw = str(value or '').strip()
    if not raw or '@' in raw and '://' not in raw:
        return ''
    if raw.startswith('//'):
        raw = 'https:' + raw
    if not raw.startswith(('http://', 'https://')):
        raw = 'https://' + raw.lstrip('/')
    parts = parse.urlsplit(raw)
    host = (parts.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    if not host or host in {'instagram.com', '2gis.kz', '2gis.com'}:
        return ''
    return raw[:500]


def _instagram_url(value: Any) -> str:
    username = normalize_instagram_identity(str(value or ''))
    if not username:
        return ''
    return f'https://www.instagram.com/{username}/'
