from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Count, Max, Q

from core.models import (
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    BuyerVehicle,
)
from core.services.buyer_contact_utils import normalize_buyer_text
from marketing.services.campaigns.live_eligibility import live_eligible_buyer_q

SORT_COUNT_ASC = 'count_asc'
SORT_COUNT_DESC = 'count_desc'
SORT_BRAND_ASC = 'brand_asc'
SORT_BRAND_DESC = 'brand_desc'

SORT_CHOICES = (
    (SORT_COUNT_ASC, 'Количество ↑'),
    (SORT_COUNT_DESC, 'Количество ↓'),
    (SORT_BRAND_ASC, 'Марка А–Я'),
    (SORT_BRAND_DESC, 'Марка Я–А'),
)


@dataclass(frozen=True)
class VehicleStatsRow:
    brand: str
    model: str
    brand_normalized: str
    model_normalized: str
    unique_buyers: int
    granted_count: int
    live_eligible_count: int
    last_request_at: datetime | None


def _base_vehicle_queryset(*, include_test: bool = False):
    queryset = BuyerVehicle.objects.select_related('buyer')
    if not include_test:
        queryset = queryset.filter(buyer__is_test_contact=False)
    return queryset


def _search_filter(queryset, search: str):
    query = (search or '').strip()
    if not query:
        return queryset
    query_norm = normalize_buyer_text(query)
    if not query_norm:
        return queryset
    return queryset.filter(
        Q(brand_normalized__icontains=query_norm)
        | Q(model_normalized__icontains=query_norm)
        | Q(brand__icontains=query)
        | Q(model__icontains=query),
    )


def get_vehicle_stats_rows(
    *,
    sort: str = SORT_COUNT_ASC,
    search: str = '',
    include_test: bool = False,
) -> list[VehicleStatsRow]:
    queryset = _base_vehicle_queryset(include_test=include_test)
    queryset = _search_filter(queryset, search)
    granted_filter = Q(
        buyer__consents__channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
        buyer__consents__purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        buyer__consents__status=CONTACT_CONSENT_STATUS_GRANTED,
    )
    live_filter = live_eligible_buyer_q(prefix='buyer__')

    aggregated = queryset.values(
        'brand',
        'model',
        'brand_normalized',
        'model_normalized',
    ).annotate(
        unique_buyers=Count('buyer_id', distinct=True),
        granted_count=Count('buyer_id', filter=granted_filter, distinct=True),
        live_eligible_count=Count('buyer_id', filter=live_filter, distinct=True),
        last_request_at=Max('last_seen_at'),
    )

    if sort == SORT_COUNT_DESC:
        aggregated = aggregated.order_by('-unique_buyers', 'brand', 'model')
    elif sort == SORT_BRAND_ASC:
        aggregated = aggregated.order_by('brand', 'model')
    elif sort == SORT_BRAND_DESC:
        aggregated = aggregated.order_by('-brand', '-model')
    else:
        aggregated = aggregated.order_by('unique_buyers', 'brand', 'model')

    return [
        VehicleStatsRow(
            brand=row['brand'],
            model=row['model'],
            brand_normalized=row['brand_normalized'],
            model_normalized=row['model_normalized'],
            unique_buyers=row['unique_buyers'],
            granted_count=row['granted_count'],
            live_eligible_count=row['live_eligible_count'],
            last_request_at=row['last_request_at'],
        )
        for row in aggregated
    ]


@dataclass(frozen=True)
class BrandModelOption:
    brand: str
    brand_normalized: str
    models: tuple[str, ...]
    total_buyers: int


def get_brand_model_tree(*, include_test: bool = False) -> list[BrandModelOption]:
    rows = get_vehicle_stats_rows(include_test=include_test)
    grouped: dict[str, dict] = {}
    for row in rows:
        bucket = grouped.setdefault(
            row.brand_normalized,
            {
                'brand': row.brand,
                'models': {},
                'total_buyers': 0,
            },
        )
        bucket['models'][row.model_normalized] = row.model
        bucket['total_buyers'] += row.unique_buyers
    options: list[BrandModelOption] = []
    for brand_norm in sorted(grouped.keys(), key=lambda item: grouped[item]['brand'].casefold()):
        bucket = grouped[brand_norm]
        models = tuple(
            bucket['models'][model_norm]
            for model_norm in sorted(bucket['models'].keys(), key=lambda item: bucket['models'][item].casefold())
        )
        options.append(
            BrandModelOption(
                brand=bucket['brand'],
                brand_normalized=brand_norm,
                models=models,
                total_buyers=bucket['total_buyers'],
            ),
        )
    return options

