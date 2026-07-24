from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from django.utils import timezone

from core.models import (
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
)
from core.services.buyer_audience_service import build_buyer_audience_queryset
from core.services.buyer_contact_utils import normalize_buyer_text
from marketing.services.contacts import (
    MarketingContact,
    ROLE_DETAILING,
    ROLE_STO,
    role_labels,
)
from marketing.services.audiences.builders import (
    SellerSourceFlags,
    build_registry,
    build_seller_source_index,
    contact_matches_subtype,
    is_buyer_group,
    is_test_audience,
    marketplace_test_phone_keys,
)
from marketing.services.audiences.constants import (
    EXCLUSION_LABELS,
    PREVIEW_LIMIT,
    SUBTYPE_MARKETPLACE_PAID,
    SUBTYPE_PARTS_REQUESTS,
)
from marketing.services.audiences.filters import (
    activity_period_start,
    category_period_start,
    normalize_marketing_criteria,
    value_in_list,
    values_intersect,
)
from marketing.services.audiences.validation import validate_and_normalize_criteria
from marketing.services.campaigns.purpose import apply_purpose_to_snapshot_status


@dataclass(frozen=True)
class AudiencePreviewRow:
    masked_phone: str
    name: str
    city: str
    roles_display: str
    brand_model: str
    last_activity: str
    consent_label: str
    eligibility_label: str


@dataclass(frozen=True)
class AudienceCalculationResult:
    matched_count: int
    unique_phones: int
    invalid_phones: int
    duplicate_count: int
    test_count: int
    inactive_count: int
    granted_count: int
    unknown_count: int
    revoked_count: int
    consent_not_recorded_count: int
    eligible_count: int
    marketplace_real_count: int
    marketplace_test_count: int
    preview_rows: tuple[AudiencePreviewRow, ...]


@dataclass(frozen=True)
class AudienceSnapshotContact:
    phone_normalized: str
    display_name: str
    city: str
    roles: list[str]
    vehicle_summary: str
    last_activity_at: datetime | None
    is_test_contact: bool
    consent_status: str
    eligibility_status: str
    exclusion_reason: str
    source_summary: dict[str, object]


@dataclass(frozen=True)
class AudienceSnapshotResult:
    contacts: tuple[AudienceSnapshotContact, ...]
    matched_count: int
    unique_count: int
    eligible_count: int
    excluded_count: int
    test_count: int
    invalid_phone_count: int
    duplicate_count: int
    inactive_count: int
    consent_granted_count: int
    consent_unknown_count: int
    consent_revoked_count: int
    consent_not_recorded_count: int
    calculation: AudienceCalculationResult


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _matches_activity_window(contact: MarketingContact, criteria: dict) -> bool:
    period_start = activity_period_start(criteria.get('activity_period') or '')
    activity_from = _parse_date(criteria.get('activity_from'))
    activity_to = _parse_date(criteria.get('activity_to'))
    if not any([period_start, activity_from, activity_to]):
        return True
    if contact.last_activity is None:
        return False
    activity_date = contact.last_activity.date()
    if period_start and contact.last_activity < period_start:
        return False
    if activity_from and activity_date < activity_from:
        return False
    if activity_to and activity_date > activity_to:
        return False
    return True


def _matches_category_period(contact: MarketingContact, criteria: dict) -> bool:
    categories = criteria.get('categories') or []
    category_period = criteria.get('category_period') or ''
    category_source = criteria.get('category_source') or 'request'
    if not categories and not category_period:
        return True
    if category_source == 'purchase':
        return not categories
    if not contact.category_interests:
        return not categories
    period_start = category_period_start(category_period)
    category_norms = {normalize_buyer_text(item) for item in categories}
    for interest in contact.category_interests:
        if category_norms and interest.category_normalized not in category_norms:
            continue
        if period_start:
            if not interest.last_seen_at or interest.last_seen_at < period_start:
                continue
        return True
    return not categories


def _matches_general_criteria(
    contact: MarketingContact,
    criteria: dict,
    *,
    seller_flags: SellerSourceFlags | None,
    contact_subtype: str = '',
    parts_db_filtered: bool = False,
) -> bool:
    if criteria.get('countries') and not value_in_list(contact.country, criteria['countries']):
        return False

    if not parts_db_filtered:
        if criteria.get('primary_cities') and not value_in_list(
            contact.primary_city or contact.city,
            criteria['primary_cities'],
        ):
            return False

        if criteria.get('search_cities') and not values_intersect(
            contact.search_cities,
            criteria['search_cities'],
        ):
            return False

        if criteria.get('brands') and not values_intersect(contact.brands, criteria['brands']):
            return False
        if criteria.get('models') and not values_intersect(contact.models, criteria['models']):
            return False
        if criteria.get('transport_types'):
            if not contact.transport_types & set(criteria['transport_types']):
                return False
        if not _matches_category_period(contact, criteria):
            return False

        request_min = criteria.get('request_count_min')
        request_max = criteria.get('request_count_max')
        if request_min is not None:
            if contact.requests_count is None or contact.requests_count < request_min:
                return False
        if request_max is not None:
            if contact.requests_count is None or contact.requests_count > request_max:
                return False

    if criteria.get('cities') and not value_in_list(contact.city, criteria['cities']):
        return False
    if criteria.get('is_active') is True and not contact.is_active:
        return False
    if criteria.get('is_active') is False and contact.is_active:
        return False
    if criteria.get('is_test') is True and not contact.is_test:
        return False
    if criteria.get('is_test') is False and contact.is_test:
        return False
    activity_already_filtered = parts_db_filtered and (
        criteria.get('activity_period')
        or criteria.get('activity_from')
        or criteria.get('activity_to')
    )
    if not activity_already_filtered and not _matches_activity_window(contact, criteria):
        return False

    orders_min = criteria.get('orders_count_min')
    orders_max = criteria.get('orders_count_max')
    if orders_min is not None:
        if contact.orders_count is None or contact.orders_count < orders_min:
            return False
    if orders_max is not None:
        if contact.orders_count is None or contact.orders_count > orders_max:
            return False

    products_min = criteria.get('products_count_min')
    products_max = criteria.get('products_count_max')
    if products_min is not None:
        if contact.products_count is None or contact.products_count < products_min:
            return False
    if products_max is not None:
        if contact.products_count is None or contact.products_count > products_max:
            return False

    if seller_flags is not None:
        if criteria.get('receive_requests') is True and seller_flags.receive_requests is not True:
            return False
        if criteria.get('receive_requests') is False and seller_flags.receive_requests is not False:
            return False
        if criteria.get('is_paused') is True and seller_flags.is_paused is not True:
            return False
        if criteria.get('is_paused') is False and seller_flags.is_paused is not False:
            return False
        if criteria.get('has_products') is True and not seller_flags.has_products:
            return False
        if criteria.get('has_products') is False and seller_flags.has_products:
            return False
        if criteria.get('has_active_products') is True and not seller_flags.has_active_products:
            return False
        if criteria.get('has_active_products') is False and seller_flags.has_active_products:
            return False
        if criteria.get('has_logo') is True and not seller_flags.has_logo:
            return False
        if criteria.get('has_logo') is False and seller_flags.has_logo:
            return False
        if criteria.get('has_instagram') is True and not seller_flags.has_instagram:
            return False
        if criteria.get('has_instagram') is False and seller_flags.has_instagram:
            return False
        if criteria.get('has_website') is True and not seller_flags.has_website:
            return False
        if criteria.get('has_website') is False and seller_flags.has_website:
            return False
        if criteria.get('has_address') is True and not seller_flags.has_address:
            return False
        if criteria.get('has_address') is False and seller_flags.has_address:
            return False
        if criteria.get('has_map_link') is True and not seller_flags.has_map_link:
            return False
        if criteria.get('has_map_link') is False and seller_flags.has_map_link:
            return False

    if criteria.get('district'):
        district_norm = normalize_buyer_text(criteria['district'])
        district_values = {normalize_buyer_text(item) for item in contact.districts if item}
        if seller_flags and seller_flags.district:
            district_values.add(normalize_buyer_text(seller_flags.district))
        if district_norm not in district_values:
            return False

    service_ids = set(criteria.get('services') or [])
    if service_ids:
        available_services = set(contact.service_ids)
        if seller_flags is not None:
            available_services |= set(seller_flags.service_ids)
        if not (available_services & service_ids):
            return False

    service_type = criteria.get('service_type') or ''
    if service_type == 'sto' and ROLE_STO not in contact.roles:
        return False
    if service_type == 'detailing' and ROLE_DETAILING not in contact.roles:
        return False

    return True


def _parts_request_phone_keys(criteria: dict) -> set[str] | None:
    vehicle_selection = criteria.get('vehicle_selection') or []
    uses_linked_requests = bool(vehicle_selection)
    buyer_criteria = {
        'countries': criteria.get('countries'),
        'cities': criteria.get('primary_cities'),
        'transport_types': criteria.get('transport_types'),
        'brands': criteria.get('brands'),
        'models': criteria.get('models'),
        'vehicle_selection': vehicle_selection,
        'categories': criteria.get('categories'),
        'search_scopes': criteria.get('search_scopes'),
        'activity_period': criteria.get('activity_period') or '',
        'request_count_min': criteria.get('request_count_min'),
        'request_count_max': criteria.get('request_count_max'),
        'search_cities': criteria.get('search_cities') or [],
        'category_period': criteria.get('category_period') or '',
    }
    has_buyer_filters = any(
        buyer_criteria.get(key)
        for key in (
            'countries',
            'cities',
            'transport_types',
            'brands',
            'models',
            'categories',
            'search_scopes',
        )
    ) or bool(buyer_criteria.get('vehicle_selection')) or buyer_criteria.get('activity_period') or buyer_criteria.get('request_count_min') is not None
    has_search_city_filters = bool(criteria.get('search_cities')) and not uses_linked_requests
    if not has_buyer_filters and not has_search_city_filters:
        return None

    queryset = build_buyer_audience_queryset(buyer_criteria)
    if criteria.get('search_cities') and not uses_linked_requests:
        search_norms = {normalize_buyer_text(city) for city in criteria['search_cities']}
        queryset = queryset.filter(city_interests__city_normalized__in=search_norms).distinct()

    return set(queryset.values_list('phone_normalized', flat=True))


def _classify_eligibility(
    contact: MarketingContact,
    *,
    contact_group: str,
    contact_subtype: str,
    test_marketplace_keys: frozenset[str],
) -> str:
    if len(contact.phone_key) != 11 or not contact.phone_key.isdigit():
        return 'invalid_phone'
    if is_test_audience(contact_group, contact_subtype):
        if contact.is_test and contact.is_active and contact.marketing_consent == CONTACT_CONSENT_STATUS_GRANTED:
            return 'eligible'
        if contact.is_test:
            return 'test_contact'
        return 'consent_unknown'
    if contact.is_test:
        return 'test_contact'
    if (
        contact_subtype == SUBTYPE_MARKETPLACE_PAID
        and contact.phone_key in test_marketplace_keys
    ):
        return 'test_contact'
    if not contact.is_active:
        return 'inactive'
    if is_buyer_group(contact_group):
        consent = contact.marketing_consent
        if consent == CONTACT_CONSENT_STATUS_GRANTED:
            return 'eligible'
        if consent == CONTACT_CONSENT_STATUS_REVOKED:
            return 'consent_revoked'
        if consent == CONTACT_CONSENT_STATUS_UNKNOWN:
            return 'consent_unknown'
        return 'consent_unknown'
    return 'consent_not_recorded'


def _format_brand_model(contact: MarketingContact) -> str:
    brands = ', '.join(sorted(contact.brands))
    models = ', '.join(sorted(contact.models))
    if brands and models:
        return f'{brands} / {models}'
    return brands or models or '—'


def _format_last_activity(contact: MarketingContact) -> str:
    if not contact.last_activity:
        return '—'
    return timezone.localtime(contact.last_activity).strftime('%d.%m.%Y %H:%M')


def calculate_audience(
    *,
    contact_group: str,
    contact_subtype: str,
    criteria: dict,
    registry: dict[str, MarketingContact] | None = None,
    seller_index: dict[str, SellerSourceFlags] | None = None,
) -> AudienceCalculationResult:
    criteria = validate_and_normalize_criteria(
        criteria,
        contact_group=contact_group,
        contact_subtype=contact_subtype,
        reject_unknown=False,
    )
    registry = registry or build_registry()
    seller_index = seller_index or build_seller_source_index()
    parts_keys = (
        _parts_request_phone_keys(criteria)
        if contact_subtype == SUBTYPE_PARTS_REQUESTS
        else None
    )
    test_marketplace_keys = marketplace_test_phone_keys()

    matched: list[MarketingContact] = []
    for phone_key, contact in registry.items():
        if not contact_matches_subtype(
            contact,
            contact_group=contact_group,
            contact_subtype=contact_subtype,
        ):
            continue
        if parts_keys is not None and phone_key not in parts_keys:
            continue
        seller_flags = seller_index.get(phone_key)
        if not _matches_general_criteria(
            contact,
            criteria,
            seller_flags=seller_flags,
            contact_subtype=contact_subtype,
            parts_db_filtered=parts_keys is not None,
        ):
            continue
        matched.append(contact)

    counts = {
        'invalid_phones': 0,
        'test_count': 0,
        'inactive_count': 0,
        'granted_count': 0,
        'unknown_count': 0,
        'revoked_count': 0,
        'consent_not_recorded_count': 0,
        'eligible_count': 0,
        'marketplace_real_count': 0,
        'marketplace_test_count': 0,
    }
    preview_rows: list[AudiencePreviewRow] = []

    for contact in sorted(
        matched,
        key=lambda item: (
            item.last_activity is None,
            -(item.last_activity.timestamp() if item.last_activity else 0),
            item.phone_key,
        ),
    ):
        eligibility = _classify_eligibility(
            contact,
            contact_group=contact_group,
            contact_subtype=contact_subtype,
            test_marketplace_keys=test_marketplace_keys,
        )
        if contact.is_test:
            counts['test_count'] += 1
        if eligibility == 'invalid_phone':
            counts['invalid_phones'] += 1
        elif eligibility == 'test_contact':
            pass
        elif eligibility == 'inactive':
            counts['inactive_count'] += 1
        elif eligibility == 'consent_revoked':
            counts['revoked_count'] += 1
        elif eligibility == 'consent_unknown':
            counts['unknown_count'] += 1
        elif eligibility == 'consent_not_recorded':
            counts['consent_not_recorded_count'] += 1
        elif eligibility == 'eligible':
            counts['eligible_count'] += 1
            if contact.marketing_consent == CONTACT_CONSENT_STATUS_GRANTED:
                counts['granted_count'] += 1

        if contact_subtype == SUBTYPE_MARKETPLACE_PAID:
            if contact.phone_key in test_marketplace_keys:
                counts['marketplace_test_count'] += 1
            else:
                counts['marketplace_real_count'] += 1

        if len(preview_rows) < PREVIEW_LIMIT:
            preview_rows.append(
                AudiencePreviewRow(
                    masked_phone=contact.masked_phone,
                    name=contact.name or '—',
                    city=contact.city or '—',
                    roles_display=', '.join(role_labels(contact)) or '—',
                    brand_model=_format_brand_model(contact),
                    last_activity=_format_last_activity(contact),
                    consent_label=contact.marketing_consent_label,
                    eligibility_label=EXCLUSION_LABELS.get(
                        eligibility,
                        eligibility,
                    ),
                ),
            )

    matched_count = len(matched)
    return AudienceCalculationResult(
        matched_count=matched_count,
        unique_phones=matched_count,
        invalid_phones=counts['invalid_phones'],
        duplicate_count=0,
        test_count=counts['test_count'],
        inactive_count=counts['inactive_count'],
        granted_count=counts['granted_count'],
        unknown_count=counts['unknown_count'],
        revoked_count=counts['revoked_count'],
        consent_not_recorded_count=counts['consent_not_recorded_count'],
        eligible_count=counts['eligible_count'],
        marketplace_real_count=counts['marketplace_real_count'],
        marketplace_test_count=counts['marketplace_test_count'],
        preview_rows=tuple(preview_rows),
    )


def _safe_phone_key(phone_key: str) -> bool:
    return len(phone_key) == 11 and phone_key.isdigit()


def _eligibility_to_snapshot_fields(eligibility: str) -> tuple[str, str]:
    if eligibility == 'eligible':
        return 'eligible', ''
    return 'excluded', eligibility


def _build_source_summary(contact: MarketingContact) -> dict[str, object]:
    return {
        'roles': sorted(contact.roles),
        'contact_status': contact.contact_status,
        'is_test': contact.is_test,
        'is_active': contact.is_active,
    }


def _match_audience_contacts(
    *,
    contact_group: str,
    contact_subtype: str,
    criteria: dict,
    registry: dict[str, MarketingContact] | None = None,
    seller_index: dict[str, SellerSourceFlags] | None = None,
) -> list[MarketingContact]:
    criteria = validate_and_normalize_criteria(
        criteria,
        contact_group=contact_group,
        contact_subtype=contact_subtype,
        reject_unknown=False,
    )
    registry = registry or build_registry()
    seller_index = seller_index or build_seller_source_index()
    parts_keys = (
        _parts_request_phone_keys(criteria)
        if contact_subtype == SUBTYPE_PARTS_REQUESTS
        else None
    )

    matched: list[MarketingContact] = []
    for phone_key, contact in registry.items():
        if not contact_matches_subtype(
            contact,
            contact_group=contact_group,
            contact_subtype=contact_subtype,
        ):
            continue
        if parts_keys is not None and phone_key not in parts_keys:
            continue
        seller_flags = seller_index.get(phone_key)
        if not _matches_general_criteria(
            contact,
            criteria,
            seller_flags=seller_flags,
            contact_subtype=contact_subtype,
            parts_db_filtered=parts_keys is not None,
        ):
            continue
        matched.append(contact)
    return matched


def collect_audience_snapshot(
    *,
    contact_group: str,
    contact_subtype: str,
    criteria: dict,
    purpose: str,
    registry: dict[str, MarketingContact] | None = None,
    seller_index: dict[str, SellerSourceFlags] | None = None,
) -> AudienceSnapshotResult:
    calculation = calculate_audience(
        contact_group=contact_group,
        contact_subtype=contact_subtype,
        criteria=criteria,
        registry=registry,
        seller_index=seller_index,
    )
    matched = _match_audience_contacts(
        contact_group=contact_group,
        contact_subtype=contact_subtype,
        criteria=criteria,
        registry=registry,
        seller_index=seller_index,
    )
    test_marketplace_keys = marketplace_test_phone_keys()

    snapshot_rows: list[AudienceSnapshotContact] = []
    seen_phones: set[str] = set()
    duplicate_count = 0

    counts = {
        'eligible_count': 0,
        'test_count': 0,
        'invalid_phone_count': 0,
        'inactive_count': 0,
        'consent_granted_count': 0,
        'consent_unknown_count': 0,
        'consent_revoked_count': 0,
        'consent_not_recorded_count': 0,
    }

    for contact in sorted(
        matched,
        key=lambda item: (
            item.last_activity is None,
            -(item.last_activity.timestamp() if item.last_activity else 0),
            item.phone_key,
        ),
    ):
        eligibility = _classify_eligibility(
            contact,
            contact_group=contact_group,
            contact_subtype=contact_subtype,
            test_marketplace_keys=test_marketplace_keys,
        )
        if contact.is_test:
            counts['test_count'] += 1
        if eligibility == 'invalid_phone':
            counts['invalid_phone_count'] += 1
            if not _safe_phone_key(contact.phone_key):
                continue
        elif eligibility == 'test_contact':
            pass
        elif eligibility == 'inactive':
            counts['inactive_count'] += 1
        elif eligibility == 'consent_revoked':
            counts['consent_revoked_count'] += 1
        elif eligibility == 'consent_unknown':
            counts['consent_unknown_count'] += 1
        elif eligibility == 'consent_not_recorded':
            counts['consent_not_recorded_count'] += 1
        elif eligibility == 'eligible':
            counts['eligible_count'] += 1
            if contact.marketing_consent == CONTACT_CONSENT_STATUS_GRANTED:
                counts['consent_granted_count'] += 1

        if not _safe_phone_key(contact.phone_key):
            continue

        if contact.phone_key in seen_phones:
            duplicate_count += 1
            eligibility_status, exclusion_reason = 'excluded', 'duplicate'
        else:
            seen_phones.add(contact.phone_key)
            eligibility_status, exclusion_reason = _eligibility_to_snapshot_fields(eligibility)
            eligibility_status, exclusion_reason = apply_purpose_to_snapshot_status(
                contact,
                purpose,
                test_marketplace_keys=test_marketplace_keys,
                eligibility_status=eligibility_status,
                exclusion_reason=exclusion_reason,
            )

        snapshot_rows.append(
            AudienceSnapshotContact(
                phone_normalized=contact.phone_key,
                display_name=contact.name or '—',
                city=contact.city or '—',
                roles=list(role_labels(contact)),
                vehicle_summary=_format_brand_model(contact),
                last_activity_at=contact.last_activity,
                is_test_contact=contact.is_test,
                consent_status=contact.marketing_consent or '',
                eligibility_status=eligibility_status,
                exclusion_reason=exclusion_reason,
                source_summary=_build_source_summary(contact),
            ),
        )

    unique_count = len(seen_phones)
    eligible_count = sum(
        1 for row in snapshot_rows if row.eligibility_status == 'eligible'
    )
    excluded_count = unique_count - eligible_count
    return AudienceSnapshotResult(
        contacts=tuple(snapshot_rows),
        matched_count=len(matched),
        unique_count=unique_count,
        eligible_count=eligible_count,
        excluded_count=excluded_count,
        test_count=counts['test_count'],
        invalid_phone_count=counts['invalid_phone_count'],
        duplicate_count=duplicate_count,
        inactive_count=counts['inactive_count'],
        consent_granted_count=counts['consent_granted_count'],
        consent_unknown_count=counts['consent_unknown_count'],
        consent_revoked_count=counts['consent_revoked_count'],
        consent_not_recorded_count=counts['consent_not_recorded_count'],
        calculation=calculation,
    )
