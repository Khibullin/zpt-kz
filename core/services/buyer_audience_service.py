from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Exists, OuterRef, Prefetch
from django.utils import timezone

from core.buyer_contact_admin_filters import (
    build_category_summary,
    build_vehicle_summary,
    marketing_consent_label,
)
from core.models import (
    BUYER_CONTACT_STATUS_BLOCKED,
    BUYER_CONTACT_STATUS_INVALID_PHONE,
    BUYER_CONTACT_STATUS_UNSUBSCRIBED,
    BUYER_CONTACT_STATUS_WHATSAPP_UNAVAILABLE,
    BuyerAudience,
    BuyerCategoryInterest,
    BuyerContact,
    BuyerVehicle,
    ContactConsent,
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
)
from core.services.buyer_contact_utils import mask_phone, normalize_buyer_text
from core.services.buyer_request_audience_filters import (
    build_buyer_audience_queryset_via_requests,
)
from core.services.buyer_vehicle_selection import (
    normalize_vehicle_selection,
    vehicle_selection_has_filters,
)

from core.services.buyer_audience_constants import (
    AUDIENCE_ACTIVITY_DAYS,
    AUDIENCE_ACTIVITY_LAST_180_DAYS,
    AUDIENCE_ACTIVITY_LAST_365_DAYS,
    AUDIENCE_ACTIVITY_LAST_30_DAYS,
    AUDIENCE_ACTIVITY_LAST_60_DAYS,
    AUDIENCE_ACTIVITY_LAST_7_DAYS,
    AUDIENCE_ACTIVITY_LAST_90_DAYS,
    AUDIENCE_ACTIVITY_NO_ACTIVITY_DATE,
    AUDIENCE_ACTIVITY_OLDER_THAN_180_DAYS,
    AUDIENCE_ACTIVITY_PERIODS,
)

ALLOWED_TRANSPORT_TYPES = {'car', 'truck'}
ALLOWED_SEARCH_SCOPES = {'city', 'kazakhstan', 'custom'}

EXCLUDED_STATUSES = [
    BUYER_CONTACT_STATUS_INVALID_PHONE,
    BUYER_CONTACT_STATUS_WHATSAPP_UNAVAILABLE,
    BUYER_CONTACT_STATUS_UNSUBSCRIBED,
    BUYER_CONTACT_STATUS_BLOCKED,
]

SAMPLE_LIMIT = 50

EMPTY_AUDIENCE_CRITERIA: dict = {
    'countries': [],
    'cities': [],
    'transport_types': [],
    'brands': [],
    'models': [],
    'categories': [],
    'search_scopes': [],
    'activity_period': '',
    'request_count_min': None,
    'request_count_max': None,
}


@dataclass(frozen=True)
class BuyerAudienceSampleContact:
    id: int
    masked_phone: str
    primary_city: str
    requests_count: int
    last_request_at: datetime | None
    vehicles_summary: str
    categories_summary: str
    marketing_consent_status: str


@dataclass(frozen=True)
class BuyerAudiencePreview:
    matched_count: int
    excluded_test_count: int
    excluded_status_count: int
    base_eligible_count: int
    marketing_granted_count: int
    marketing_unknown_count: int
    marketing_revoked_count: int
    marketing_missing_count: int
    final_recipient_count: int
    sample_contacts: tuple[BuyerAudienceSampleContact, ...]


def eligible_buyer_contacts():
    """
    Это базовый QuerySet для будущего конструктора аудиторий.
    Перед рекламной отправкой дополнительно обязательно проверяется
    marketing consent = granted.
    """
    return BuyerContact.objects.filter(is_test_contact=False).exclude(
        status__in=EXCLUDED_STATUSES,
    )


def _clean_string_list(values: object, *, normalize: bool = False) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item or '').strip()
        if not text:
            continue
        if normalize:
            text = normalize_buyer_text(text)
            if not text:
                continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def _parse_non_negative_int(value: object) -> int | None:
    if value is None or value == '':
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def normalize_audience_criteria(raw_criteria: object) -> dict:
    if not isinstance(raw_criteria, dict):
        return dict(EMPTY_AUDIENCE_CRITERIA)

    transport_types = [
        value
        for value in _clean_string_list(raw_criteria.get('transport_types'))
        if value in ALLOWED_TRANSPORT_TYPES
    ]
    search_scopes = [
        value
        for value in _clean_string_list(raw_criteria.get('search_scopes'))
        if value in ALLOWED_SEARCH_SCOPES
    ]
    activity_period = str(raw_criteria.get('activity_period') or '').strip()
    if activity_period not in AUDIENCE_ACTIVITY_PERIODS:
        activity_period = ''

    request_count_min = _parse_non_negative_int(raw_criteria.get('request_count_min'))
    request_count_max = _parse_non_negative_int(raw_criteria.get('request_count_max'))
    if (
        request_count_min is not None
        and request_count_max is not None
        and request_count_min > request_count_max
    ):
        request_count_min = None
        request_count_max = None

    vehicle_selection = normalize_vehicle_selection(raw_criteria.get('vehicle_selection'))

    return {
        'countries': _clean_string_list(raw_criteria.get('countries')),
        'cities': _clean_string_list(raw_criteria.get('cities')),
        'transport_types': transport_types,
        'brands': _clean_string_list(raw_criteria.get('brands'), normalize=True),
        'models': _clean_string_list(raw_criteria.get('models'), normalize=True),
        'categories': _clean_string_list(raw_criteria.get('categories'), normalize=True),
        'search_scopes': search_scopes,
        'activity_period': activity_period,
        'request_count_min': request_count_min,
        'request_count_max': request_count_max,
        'vehicle_selection': vehicle_selection,
    }


def audience_criteria_has_filters(criteria: dict) -> bool:
    normalized = normalize_audience_criteria(criteria)
    if normalized.get('vehicle_selection'):
        if vehicle_selection_has_filters(normalized['vehicle_selection']):
            return True
    for key in (
        'countries',
        'cities',
        'transport_types',
        'brands',
        'models',
        'categories',
        'search_scopes',
    ):
        if normalized.get(key):
            return True
    if normalized.get('activity_period'):
        return True
    if normalized.get('request_count_min') is not None:
        return True
    if normalized.get('request_count_max') is not None:
        return True
    return False


def _filter_case_insensitive_field(queryset, field_name: str, values: list[str]):
    if not values:
        return queryset
    folded_values = {value.casefold() for value in values}
    distinct_values = (
        BuyerContact.objects.exclude(**{f'{field_name}__exact': ''})
        .values_list(field_name, flat=True)
        .distinct()
    )
    matched_values = [
        value
        for value in distinct_values
        if value and value.casefold() in folded_values
    ]
    if not matched_values:
        return queryset.none()
    return queryset.filter(**{f'{field_name}__in': matched_values})


def _apply_activity_filter(queryset, activity_period: str):
    if not activity_period:
        return queryset
    now = timezone.now()
    if activity_period == AUDIENCE_ACTIVITY_NO_ACTIVITY_DATE:
        return queryset.filter(last_request_at__isnull=True)
    if activity_period == AUDIENCE_ACTIVITY_OLDER_THAN_180_DAYS:
        return queryset.filter(last_request_at__lt=now - timedelta(days=180))
    days = AUDIENCE_ACTIVITY_DAYS.get(activity_period)
    if days is None:
        return queryset
    return queryset.filter(last_request_at__gte=now - timedelta(days=days))


def build_buyer_audience_queryset(criteria: dict):
    normalized = normalize_audience_criteria(criteria)
    vehicle_selection = normalized.get('vehicle_selection') or []
    if vehicle_selection_has_filters(vehicle_selection):
        return build_buyer_audience_queryset_via_requests(criteria, normalized)

    queryset = BuyerContact.objects.all()

    if normalized['countries']:
        queryset = _filter_case_insensitive_field(
            queryset,
            'primary_country',
            normalized['countries'],
        )
    if normalized['cities']:
        queryset = _filter_case_insensitive_field(
            queryset,
            'primary_city',
            normalized['cities'],
        )

    vehicle_filters: dict[str, object] = {}
    if normalized['transport_types']:
        vehicle_filters['vehicles__transport_type__in'] = normalized['transport_types']
    if normalized['brands']:
        vehicle_filters['vehicles__brand_normalized__in'] = normalized['brands']
    if normalized['models']:
        vehicle_filters['vehicles__model_normalized__in'] = normalized['models']
    if vehicle_filters:
        queryset = queryset.filter(**vehicle_filters).distinct()

    if normalized['categories']:
        queryset = queryset.filter(
            category_interests__category_normalized__in=normalized['categories'],
        ).distinct()

    if normalized['search_scopes']:
        queryset = queryset.filter(
            last_search_scope__in=normalized['search_scopes'],
        )

    queryset = _apply_activity_filter(queryset, normalized['activity_period'])

    if normalized['request_count_min'] is not None:
        queryset = queryset.filter(
            requests_count__gte=normalized['request_count_min'],
        )
    if normalized['request_count_max'] is not None:
        queryset = queryset.filter(
            requests_count__lte=normalized['request_count_max'],
        )

    return queryset


def _marketing_consent_subquery():
    return ContactConsent.objects.filter(
        buyer=OuterRef('pk'),
        channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
        purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
    )


def _build_sample_contacts(queryset) -> tuple[BuyerAudienceSampleContact, ...]:
    marketing_consents = ContactConsent.objects.filter(
        channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
        purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
    )
    sample_qs = queryset.prefetch_related(
        Prefetch(
            'vehicles',
            queryset=BuyerVehicle.objects.order_by('-last_seen_at', '-id'),
        ),
        Prefetch(
            'category_interests',
            queryset=BuyerCategoryInterest.objects.order_by('-last_seen_at', '-id'),
        ),
        Prefetch(
            'consents',
            queryset=marketing_consents,
            to_attr='marketing_consents',
        ),
    ).order_by('-last_request_at', '-id')[:SAMPLE_LIMIT]

    samples: list[BuyerAudienceSampleContact] = []
    for buyer in sample_qs:
        consents = getattr(buyer, 'marketing_consents', None)
        consent_status = consents[0].status if consents else None
        samples.append(
            BuyerAudienceSampleContact(
                id=buyer.pk,
                masked_phone=mask_phone(buyer.phone_normalized),
                primary_city=buyer.primary_city,
                requests_count=buyer.requests_count,
                last_request_at=buyer.last_request_at,
                vehicles_summary=build_vehicle_summary(buyer.vehicles.all()),
                categories_summary=build_category_summary(
                    buyer.category_interests.all(),
                ),
                marketing_consent_status=marketing_consent_label(consent_status),
            ),
        )
    return tuple(samples)


def preview_buyer_audience(audience: BuyerAudience) -> BuyerAudiencePreview:
    criteria = normalize_audience_criteria(audience.criteria)
    matched = build_buyer_audience_queryset(criteria)

    matched_count = matched.count()
    excluded_test_count = matched.filter(is_test_contact=True).count()
    excluded_status_count = matched.filter(
        is_test_contact=False,
        status__in=EXCLUDED_STATUSES,
    ).count()

    base_eligible = eligible_buyer_contacts().filter(
        pk__in=matched.values('pk'),
    )
    base_eligible_count = base_eligible.count()

    marketing = _marketing_consent_subquery()
    marketing_granted_count = base_eligible.filter(
        Exists(marketing.filter(status=CONTACT_CONSENT_STATUS_GRANTED)),
    ).count()
    marketing_revoked_count = base_eligible.filter(
        Exists(marketing.filter(status=CONTACT_CONSENT_STATUS_REVOKED)),
    ).count()
    marketing_unknown_count = base_eligible.filter(
        Exists(marketing.filter(status=CONTACT_CONSENT_STATUS_UNKNOWN)),
    ).count()
    marketing_missing_count = base_eligible.filter(
        ~Exists(marketing),
    ).count()

    final_recipients = base_eligible.filter(
        Exists(marketing.filter(status=CONTACT_CONSENT_STATUS_GRANTED)),
    )
    final_recipient_count = final_recipients.count()
    sample_contacts = _build_sample_contacts(final_recipients)

    return BuyerAudiencePreview(
        matched_count=matched_count,
        excluded_test_count=excluded_test_count,
        excluded_status_count=excluded_status_count,
        base_eligible_count=base_eligible_count,
        marketing_granted_count=marketing_granted_count,
        marketing_unknown_count=marketing_unknown_count,
        marketing_revoked_count=marketing_revoked_count,
        marketing_missing_count=marketing_missing_count,
        final_recipient_count=final_recipient_count,
        sample_contacts=sample_contacts,
    )
