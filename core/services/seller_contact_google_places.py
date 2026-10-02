"""Google Places API (New) as a locator only.

The response may be used in memory to decide whether a place matches a
SellerLead and to obtain a website URL for a separate fetch. Phone, address,
display name, coordinates, and websiteUri from Google are not stored.
The only durable Google value is the Place ID.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, parse, request

from django.conf import settings

from core.services.seller_contact_website import distinctive_name_tokens, registrable_domain
from core.services.seller_discovery_identity import normalize_address, normalize_domain, normalize_seller_name

logger = logging.getLogger(__name__)

PLACES_SEARCH_URL = 'https://places.googleapis.com/v1/places:searchText'
PLACES_FIELD_MASK = 'places.id,places.displayName,places.formattedAddress,places.websiteUri'
PLACE_DETAILS_FIELD_MASK = 'id,displayName,formattedAddress,websiteUri'
MAX_RESULTS = 5
FETCH_TIMEOUT = 8.0
MATCH_THRESHOLD = 70
MATCH_GAP = 15
_FORBIDDEN_MASK_TOKENS = (
    'nationalphonenumber',
    'internationalphonenumber',
    'reviews',
    'photos',
    'regularopeninghours',
    'location',
)


class GooglePlacesError(Exception):
    """Places API failed. The message never contains the API key."""


@dataclass(frozen=True)
class GooglePlaceLocator:
    """Transient match. website_uri must not be written to the database."""

    place_id: str
    website_uri: str
    ambiguous: bool = False
    error: str = ''


def google_places_enabled() -> bool:
    return bool(getattr(settings, 'SELLER_CONTACT_ENRICHMENT_ENABLED', False)) and bool(
        getattr(settings, 'SELLER_CONTACT_GOOGLE_PLACES_ENABLED', False),
    )


def locate_google_place(
    *,
    name: str,
    city: str = '',
    address: str = '',
    website_url: str = '',
    urlopen: Callable[..., Any] | None = None,
) -> GooglePlaceLocator:
    """One Text Search and, only if the winner has no website, one Details call."""
    if not google_places_enabled():
        raise GooglePlacesError(
            'Google Places выключен. Нужны SELLER_CONTACT_ENRICHMENT_ENABLED и '
            'SELLER_CONTACT_GOOGLE_PLACES_ENABLED.',
        )
    api_key = (getattr(settings, 'GOOGLE_PLACES_API_KEY', '') or '').strip()
    if not api_key:
        raise GooglePlacesError('GOOGLE_PLACES_API_KEY не задан.')
    opener = urlopen or _urlopen_without_proxy
    query = ' '.join(part for part in (name, city) if part).strip()
    places = _search_places(query, api_key=api_key, opener=opener)
    ranked = []
    for place in places:
        score = _match_score(
            place,
            lead_name=name,
            city=city,
            address=address,
            website_url=website_url,
        )
        if score:
            ranked.append((score, place))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked or ranked[0][0] < MATCH_THRESHOLD:
        return GooglePlaceLocator(place_id='', website_uri='', error='нет уверенного совпадения')
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < MATCH_GAP:
        return GooglePlaceLocator(place_id='', website_uri='', ambiguous=True, error='неоднозначное совпадение')
    winner = ranked[0][1]
    place_id = str(winner.get('id') or '').strip()
    website_uri = _website_uri(winner)
    if place_id and not website_uri:
        details = _place_details(place_id, api_key=api_key, opener=opener)
        website_uri = _website_uri(details)
    return GooglePlaceLocator(place_id=place_id, website_uri=website_uri)


def _search_places(query: str, *, api_key: str, opener: Callable[..., Any]) -> list[dict[str, Any]]:
    body = json.dumps({
        'textQuery': query,
        'pageSize': MAX_RESULTS,
        'languageCode': 'ru',
        'regionCode': 'KZ',
    }).encode('utf-8')
    http_request = request.Request(
        PLACES_SEARCH_URL,
        data=body,
        headers=_places_headers(api_key, PLACES_FIELD_MASK),
        method='POST',
    )
    logger.info('Google Places text search query=%r', query)
    payload = _read_json(http_request, opener=opener, api_key=api_key)
    places = payload.get('places') or []
    return [place for place in places if isinstance(place, dict)]


def _place_details(place_id: str, *, api_key: str, opener: Callable[..., Any]) -> dict[str, Any]:
    safe_id = parse.quote(place_id, safe='')
    http_request = request.Request(
        f'https://places.googleapis.com/v1/places/{safe_id}',
        headers=_places_headers(api_key, PLACE_DETAILS_FIELD_MASK),
        method='GET',
    )
    logger.info('Google Places details id_present=%s', bool(place_id))
    payload = _read_json(http_request, opener=opener, api_key=api_key)
    return payload if isinstance(payload, dict) else {}


def _places_headers(api_key: str, field_mask: str) -> dict[str, str]:
    lowered = field_mask.casefold()
    if any(token in lowered for token in _FORBIDDEN_MASK_TOKENS):
        raise GooglePlacesError('Field mask Places запрашивает лишние поля.')
    return {
        'Content-Type': 'application/json',
        'X-Goog-Api-Key': api_key,
        'X-Goog-FieldMask': field_mask,
    }


def _read_json(http_request: request.Request, *, opener: Callable[..., Any], api_key: str) -> dict[str, Any]:
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
            raise GooglePlacesError('Google Places API timeout') from None
        raise GooglePlacesError(f'Google Places API network error: {reason}') from None
    if status == 429:
        raise GooglePlacesError('Google Places API HTTP 429') from None
    if status >= 400:
        _raise_http(status, raw, api_key=api_key)
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    try:
        payload = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        preview = _redact(raw.decode('utf-8', errors='replace')[:180], api_key=api_key)
        raise GooglePlacesError(f'Google Places вернул не JSON: {preview}') from None
    if not isinstance(payload, dict):
        raise GooglePlacesError('Google Places вернул неожиданный JSON')
    return payload


def _raise_http(status: int, raw: bytes, *, api_key: str) -> None:
    if status == 429:
        raise GooglePlacesError('Google Places API HTTP 429')
    detail = ''
    try:
        payload = json.loads(raw.decode('utf-8'))
        error_body = payload.get('error') if isinstance(payload, dict) else None
        if isinstance(error_body, dict):
            detail = str(error_body.get('message') or '')
    except (UnicodeDecodeError, json.JSONDecodeError):
        detail = raw.decode('utf-8', errors='replace')[:180]
    detail = _redact(detail, api_key=api_key)
    message = f'Google Places API HTTP {status}'
    if detail:
        message = f'{message}: {detail}'
    raise GooglePlacesError(message)


def _redact(text: str, *, api_key: str) -> str:
    redacted = str(text or '')
    if api_key:
        redacted = redacted.replace(api_key, '[REDACTED]')
    redacted = re.sub(r'(key=)[^&\s]+', r'\1[REDACTED]', redacted, flags=re.IGNORECASE)
    return redacted


def _website_uri(place: dict[str, Any]) -> str:
    return str(place.get('websiteUri') or '').strip()


def _display_name(place: dict[str, Any]) -> str:
    value = place.get('displayName')
    if isinstance(value, dict):
        return str(value.get('text') or '')
    return str(value or '')


def _match_score(
    place: dict[str, Any],
    *,
    lead_name: str,
    city: str,
    address: str,
    website_url: str,
) -> int:
    place_name = normalize_seller_name(_display_name(place))
    tokens = distinctive_name_tokens(lead_name)
    matched = [token for token in tokens if token and token in place_name]
    if len(tokens) >= 2 and len(matched) >= 2:
        name_score = 50
    elif len(tokens) == 1 and len(matched) == 1 and len(tokens[0]) >= 8:
        name_score = 20
    elif len(tokens) >= 2 and len(matched) == 1:
        name_score = 15
    else:
        name_score = 0
    formatted = str(place.get('formattedAddress') or '')
    city_score = 30 if city and city.casefold() in formatted.casefold() else 0
    lead_domain = registrable_domain(normalize_domain(website_url))
    place_domain = registrable_domain(parse.urlsplit(_website_uri(place)).hostname or '')
    domain_score = 45 if lead_domain and place_domain and lead_domain == place_domain else 0
    normalized_address = normalize_address(address)
    address_score = 25 if len(normalized_address) >= 8 and normalized_address in normalize_address(formatted) else 0
    if name_score == 0 and domain_score == 0:
        return 0
    return name_score + city_score + domain_score + address_score


def _urlopen_without_proxy(http_request: request.Request, timeout: float):
    opener = request.build_opener(request.ProxyHandler({}))
    return opener.open(http_request, timeout=timeout)
