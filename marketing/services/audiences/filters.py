from __future__ import annotations

from datetime import date, datetime, timedelta

from django.utils import timezone

from core.services.buyer_audience_service import (
    ALLOWED_SEARCH_SCOPES,
    ALLOWED_TRANSPORT_TYPES,
    AUDIENCE_ACTIVITY_DAYS,
    AUDIENCE_ACTIVITY_PERIODS,
    normalize_audience_criteria,
)
from core.services.buyer_contact_utils import normalize_buyer_text
from core.services.buyer_vehicle_selection import normalize_vehicle_selection
from marketing.services.audiences.constants import (
    ACTIVITY_PERIOD_CHOICES,
    CATEGORY_PERIOD_CHOICES,
    GROUP_BUYERS,
    GROUP_SERVICE_PROVIDERS,
    SEARCH_SCOPE_CHOICES,
    SUBTYPE_DETAILING,
    SUBTYPE_PARTS_REQUESTS,
    SUBTYPE_STO,
    TRANSPORT_TYPE_CHOICES,
)
from service_requests.models import Service


def service_ids_for_seller_type(seller_type: str) -> set[int]:
    return set(
        Service.objects.filter(serviceseller__seller_type=seller_type).values_list('id', flat=True),
    )


def allowed_service_ids_for_audience(
    *,
    contact_group: str,
    contact_subtype: str,
    service_type: str,
) -> set[int] | None:
    if contact_group == GROUP_SERVICE_PROVIDERS:
        if contact_subtype == SUBTYPE_STO:
            return service_ids_for_seller_type('sto')
        if contact_subtype == SUBTYPE_DETAILING:
            return service_ids_for_seller_type('detailing')
    if service_type == 'sto':
        return service_ids_for_seller_type('sto')
    if service_type == 'detailing':
        return service_ids_for_seller_type('detailing')
    return None

EMPTY_MARKETING_CRITERIA: dict = {
    'countries': [],
    'primary_cities': [],
    'search_cities': [],
    'cities': [],
    'brands': [],
    'models': [],
    'categories': [],
    'search_scopes': [],
    'transport_types': [],
    'services': [],
    'activity_from': None,
    'activity_to': None,
    'activity_period': '',
    'category_period': '',
    'category_source': 'request',
    'is_active': None,
    'is_test': None,
    'request_count_min': None,
    'request_count_max': None,
    'orders_count_min': None,
    'orders_count_max': None,
    'products_count_min': None,
    'products_count_max': None,
    'service_type': '',
    'district': '',
    'receive_requests': None,
    'is_paused': None,
    'has_products': None,
    'has_active_products': None,
    'has_logo': None,
    'has_instagram': None,
    'has_website': None,
    'has_address': None,
    'has_map_link': None,
    'vehicle_selection': [],
}


def _clean_string_list(values: object, *, limit: int = 50) -> list[str]:
    if not isinstance(values, list):
        if isinstance(values, str) and values.strip():
            return [values.strip()]
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item or '').strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def _clean_service_ids(values: object, *, limit: int = 50) -> list[int]:
    if not isinstance(values, list):
        if isinstance(values, str) and values.strip().isdigit():
            values = [values.strip()]
        else:
            return []
    valid_ids = set(Service.objects.values_list('id', flat=True))
    cleaned: list[int] = []
    seen: set[int] = set()
    for item in values:
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            continue
        if parsed <= 0 or parsed not in valid_ids or parsed in seen:
            continue
        seen.add(parsed)
        cleaned.append(parsed)
        if len(cleaned) >= limit:
            break
    return cleaned


def _parse_optional_bool(value: object) -> bool | None:
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ('true', '1', 'yes', 'on'):
        return True
    if text in ('false', '0', 'no', 'off'):
        return False
    return None


def _parse_optional_int(value: object) -> int | None:
    if value is None or value == '':
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def _parse_optional_date(value: object) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        date.fromisoformat(text)
    except ValueError:
        return None
    return text


def _empty_result() -> dict:
    return dict(EMPTY_MARKETING_CRITERIA)


def normalize_marketing_criteria(
    raw: object,
    *,
    contact_group: str = '',
    contact_subtype: str = '',
) -> dict:
    if not isinstance(raw, dict):
        return _empty_result()

    legacy_cities = _clean_string_list(raw.get('cities'))
    primary_cities = _clean_string_list(raw.get('primary_cities')) or (
        legacy_cities if contact_group == GROUP_BUYERS and contact_subtype == SUBTYPE_PARTS_REQUESTS else []
    )
    search_cities = _clean_string_list(raw.get('search_cities'))
    cities = _clean_string_list(raw.get('cities'))
    if contact_group == GROUP_BUYERS and contact_subtype == SUBTYPE_PARTS_REQUESTS:
        cities = []
    elif not cities and legacy_cities and contact_group != GROUP_BUYERS:
        cities = legacy_cities

    buyer_slice = normalize_audience_criteria({
        'countries': raw.get('countries'),
        'cities': primary_cities,
        'transport_types': raw.get('transport_types'),
        'brands': [normalize_buyer_text(item) for item in _clean_string_list(raw.get('brands'))],
        'models': [normalize_buyer_text(item) for item in _clean_string_list(raw.get('models'))],
        'categories': [normalize_buyer_text(item) for item in _clean_string_list(raw.get('categories'))],
        'search_scopes': raw.get('search_scopes'),
        'activity_period': raw.get('activity_period'),
        'request_count_min': raw.get('request_count_min'),
        'request_count_max': raw.get('request_count_max'),
    })

    activity_period = str(raw.get('activity_period') or '').strip()
    if activity_period == 'all':
        activity_period = ''
    elif activity_period not in AUDIENCE_ACTIVITY_PERIODS and activity_period not in {
        choice[0] for choice in ACTIVITY_PERIOD_CHOICES
    }:
        activity_period = buyer_slice.get('activity_period') or ''

    category_period = str(raw.get('category_period') or '').strip()
    if category_period not in {choice[0] for choice in CATEGORY_PERIOD_CHOICES}:
        category_period = ''

    category_source = str(raw.get('category_source') or 'request').strip()
    if category_source not in ('request', 'purchase', 'both'):
        category_source = 'request'

    service_type = str(raw.get('service_type') or '').strip()
    if service_type not in ('sto', 'detailing', ''):
        service_type = ''

    search_scopes = [
        value
        for value in _clean_string_list(raw.get('search_scopes') or buyer_slice.get('search_scopes'))
        if value in ALLOWED_SEARCH_SCOPES
    ]
    transport_types = [
        value
        for value in _clean_string_list(raw.get('transport_types') or buyer_slice.get('transport_types'))
        if value in ALLOWED_TRANSPORT_TYPES
    ]

    request_min = _parse_optional_int(raw.get('request_count_min'))
    request_max = _parse_optional_int(raw.get('request_count_max'))
    if request_min is not None and request_max is not None and request_min > request_max:
        request_min = request_max = None

    orders_min = _parse_optional_int(raw.get('orders_count_min'))
    orders_max = _parse_optional_int(raw.get('orders_count_max'))
    if orders_min is not None and orders_max is not None and orders_min > orders_max:
        orders_min = orders_max = None

    products_min = _parse_optional_int(raw.get('products_count_min'))
    products_max = _parse_optional_int(raw.get('products_count_max'))
    if products_min is not None and products_max is not None and products_min > products_max:
        products_min = products_max = None

    activity_from = _parse_optional_date(raw.get('activity_from'))
    activity_to = _parse_optional_date(raw.get('activity_to'))
    if activity_from and activity_to and activity_from > activity_to:
        activity_from = activity_to = None

    services = _clean_service_ids(raw.get('services'))
    allowed_service_ids = allowed_service_ids_for_audience(
        contact_group=contact_group,
        contact_subtype=contact_subtype,
        service_type=service_type,
    )
    if allowed_service_ids is not None:
        services = [service_id for service_id in services if service_id in allowed_service_ids]

    return {
        'countries': _clean_string_list(raw.get('countries')) or buyer_slice.get('countries'),
        'primary_cities': primary_cities or buyer_slice.get('cities'),
        'search_cities': search_cities,
        'cities': cities,
        'brands': _clean_string_list(raw.get('brands')),
        'models': _clean_string_list(raw.get('models')),
        'vehicle_selection': normalize_vehicle_selection(raw.get('vehicle_selection')),
        'categories': _clean_string_list(raw.get('categories')),
        'search_scopes': search_scopes,
        'transport_types': transport_types,
        'services': services,
        'activity_from': activity_from,
        'activity_to': activity_to,
        'activity_period': activity_period,
        'category_period': category_period,
        'category_source': category_source,
        'is_active': _parse_optional_bool(raw.get('is_active')),
        'is_test': _parse_optional_bool(raw.get('is_test')),
        'request_count_min': request_min if request_min is not None else buyer_slice.get('request_count_min'),
        'request_count_max': request_max if request_max is not None else buyer_slice.get('request_count_max'),
        'orders_count_min': orders_min,
        'orders_count_max': orders_max,
        'products_count_min': products_min,
        'products_count_max': products_max,
        'service_type': service_type,
        'district': str(raw.get('district') or '').strip(),
        'receive_requests': _parse_optional_bool(raw.get('receive_requests')),
        'is_paused': _parse_optional_bool(raw.get('is_paused')),
        'has_products': _parse_optional_bool(raw.get('has_products')),
        'has_active_products': _parse_optional_bool(raw.get('has_active_products')),
        'has_logo': _parse_optional_bool(raw.get('has_logo')),
        'has_instagram': _parse_optional_bool(raw.get('has_instagram')),
        'has_website': _parse_optional_bool(raw.get('has_website')),
        'has_address': _parse_optional_bool(raw.get('has_address')),
        'has_map_link': _parse_optional_bool(raw.get('has_map_link')),
    }


CRITERIA_MULTISELECT_FIELDS = (
    'countries',
    'primary_cities',
    'search_cities',
    'cities',
    'brands',
    'models',
    'categories',
    'search_scopes',
    'transport_types',
    'services',
)

CRITERIA_SCALAR_FIELDS = (
    'activity_from',
    'activity_to',
    'activity_period',
    'category_period',
    'category_source',
    'is_active',
    'is_test',
    'request_count_min',
    'request_count_max',
    'orders_count_min',
    'orders_count_max',
    'products_count_min',
    'products_count_max',
    'service_type',
    'district',
    'receive_requests',
    'is_paused',
    'has_products',
    'has_active_products',
    'has_logo',
    'has_instagram',
    'has_website',
    'has_address',
    'has_map_link',
)


def criteria_raw_from_request_post(
    post_data,
    *,
    contact_group: str = '',
    contact_subtype: str = '',
) -> dict:
    from marketing.services.audiences.validation import allowed_criteria_keys

    allowed = allowed_criteria_keys(contact_group, contact_subtype)

    def getlist(name: str) -> list[str]:
        return [value.strip() for value in post_data.getlist(name) if str(value).strip()]

    raw: dict = {}
    for name in CRITERIA_MULTISELECT_FIELDS:
        if name in post_data and name in allowed:
            raw[name] = getlist(name)
    for name in CRITERIA_SCALAR_FIELDS:
        if name in post_data and name in allowed:
            raw[name] = post_data.get(name, '')
    return raw


def criteria_from_request_post(
    post_data,
    *,
    contact_group: str = '',
    contact_subtype: str = '',
) -> dict:
    return normalize_marketing_criteria(
        criteria_raw_from_request_post(
            post_data,
            contact_group=contact_group,
            contact_subtype=contact_subtype,
        ),
        contact_group=contact_group,
        contact_subtype=contact_subtype,
    )


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def activity_period_start(period: str) -> datetime | None:
    if not period or period == 'all':
        return None
    if period in AUDIENCE_ACTIVITY_DAYS:
        return timezone.now() - timedelta(days=AUDIENCE_ACTIVITY_DAYS[period])
    try:
        days = int(period)
    except (TypeError, ValueError):
        return None
    return timezone.now() - timedelta(days=days)


def category_period_start(period: str) -> datetime | None:
    if not period or period == 'all':
        return None
    try:
        days = int(period)
    except (TypeError, ValueError):
        return None
    return timezone.now() - timedelta(days=days)


def value_in_list(value: str, allowed: list[str]) -> bool:
    if not allowed:
        return True
    if not value:
        return False
    normalized = normalize_buyer_text(value)
    return any(normalize_buyer_text(item) == normalized for item in allowed)


def values_intersect(values: set[str], allowed: list[str]) -> bool:
    if not allowed:
        return True
    if not values:
        return False
    allowed_norm = {normalize_buyer_text(item) for item in allowed}
    return bool({normalize_buyer_text(value) for value in values} & allowed_norm)


__all__ = [
    'EMPTY_MARKETING_CRITERIA',
    'normalize_marketing_criteria',
    'criteria_raw_from_request_post',
    'criteria_from_request_post',
    'activity_period_start',
    'category_period_start',
    'value_in_list',
    'values_intersect',
    'SEARCH_SCOPE_CHOICES',
    'TRANSPORT_TYPE_CHOICES',
    'CATEGORY_PERIOD_CHOICES',
    'ACTIVITY_PERIOD_CHOICES',
]
