from __future__ import annotations

from datetime import timedelta

from django.db.models import Q, QuerySet
from django.utils import timezone

from core.models import BuyerContact, Request
from core.services.buyer_audience_constants import (
    AUDIENCE_ACTIVITY_DAYS,
    AUDIENCE_ACTIVITY_NO_ACTIVITY_DATE,
    AUDIENCE_ACTIVITY_OLDER_THAN_180_DAYS,
    AUDIENCE_ACTIVITY_PERIODS,
)
from core.services.buyer_contact_utils import normalize_buyer_text
from core.services.buyer_vehicle_selection import (
    vehicle_selection_has_filters,
)


def build_request_vehicle_selection_q(selection: list[dict]) -> Q:
    combined = Q()
    for entry in selection:
        brand = entry['brand']
        if entry.get('all_models') or not entry.get('models'):
            combined |= Q(brand__iexact=brand)
            continue
        for model in entry['models']:
            combined |= Q(brand__iexact=brand, model__iexact=model)
    return combined


def _apply_request_activity_filter(queryset: QuerySet, activity_period: str) -> QuerySet:
    if not activity_period or activity_period not in AUDIENCE_ACTIVITY_PERIODS:
        return queryset
    now = timezone.now()
    if activity_period == AUDIENCE_ACTIVITY_NO_ACTIVITY_DATE:
        return queryset.none()
    if activity_period == AUDIENCE_ACTIVITY_OLDER_THAN_180_DAYS:
        return queryset.filter(created_at__lt=now - timedelta(days=180))
    days = AUDIENCE_ACTIVITY_DAYS.get(activity_period)
    if days is None:
        return queryset
    return queryset.filter(created_at__gte=now - timedelta(days=days))


def _filter_request_field_case_insensitive(
    queryset: QuerySet,
    field_name: str,
    values: list[str],
) -> QuerySet:
    if not values:
        return queryset
    folded_values = {normalize_buyer_text(value) for value in values}
    distinct_values = (
        Request.objects.exclude(**{f'{field_name}__exact': ''})
        .values_list(field_name, flat=True)
        .distinct()
    )
    matched_values = [
        value
        for value in distinct_values
        if value and normalize_buyer_text(value) in folded_values
    ]
    if not matched_values:
        return queryset.none()
    return queryset.filter(**{f'{field_name}__in': matched_values})


def filter_requests_for_vehicle_criteria(criteria: dict) -> QuerySet:
    """
    Фильтрует заявки, где brand/model, период, категория и город заявки
    относятся к одной и той же записи Request.
    """
    from core.services.buyer_audience_service import normalize_audience_criteria

    normalized = normalize_audience_criteria(criteria)
    vehicle_selection = normalized.get('vehicle_selection') or []
    if not vehicle_selection_has_filters(vehicle_selection):
        return Request.objects.none()

    queryset = Request.objects.filter(buyer_contact__isnull=False)
    queryset = queryset.filter(build_request_vehicle_selection_q(vehicle_selection))

    if normalized['transport_types']:
        queryset = queryset.filter(transport_type__in=normalized['transport_types'])

    if normalized['categories']:
        queryset = _filter_request_field_case_insensitive(
            queryset,
            'category',
            normalized['categories'],
        )

    category_period = str(criteria.get('category_period') or '').strip()
    if normalized['categories'] and category_period and category_period != 'all':
        try:
            category_days = int(category_period)
        except (TypeError, ValueError):
            category_days = None
        if category_days is not None:
            queryset = queryset.filter(
                created_at__gte=timezone.now() - timedelta(days=category_days),
            )

    queryset = _apply_request_activity_filter(queryset, normalized['activity_period'])

    search_cities = criteria.get('search_cities') or []
    if search_cities:
        queryset = _filter_request_field_case_insensitive(queryset, 'city', search_cities)

    return queryset


def build_buyer_audience_queryset_via_requests(criteria: dict, normalized: dict):
    buyer_ids = filter_requests_for_vehicle_criteria(criteria).values_list(
        'buyer_contact_id',
        flat=True,
    ).distinct()
    queryset = BuyerContact.objects.filter(pk__in=buyer_ids)

    if normalized['countries']:
        from core.services.buyer_audience_service import _filter_case_insensitive_field

        queryset = _filter_case_insensitive_field(
            queryset,
            'primary_country',
            normalized['countries'],
        )
    if normalized['cities']:
        from core.services.buyer_audience_service import _filter_case_insensitive_field

        queryset = _filter_case_insensitive_field(
            queryset,
            'primary_city',
            normalized['cities'],
        )

    if normalized['search_scopes']:
        queryset = queryset.filter(
            last_search_scope__in=normalized['search_scopes'],
        )

    if normalized['request_count_min'] is not None:
        queryset = queryset.filter(
            requests_count__gte=normalized['request_count_min'],
        )
    if normalized['request_count_max'] is not None:
        queryset = queryset.filter(
            requests_count__lte=normalized['request_count_max'],
        )

    return queryset
