from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q

from core.models import (
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    BuyerVehicle,
)
from core.services.buyer_vehicle_selection import normalize_vehicle_selection
from marketing.services.buyer_vehicles.stats import VehicleStatsRow
from marketing.services.campaigns.live_eligibility import live_eligible_buyer_q

TABLE_ROW_KEY_SEPARATOR = ':'


class TableSelectionError(ValueError):
    pass


@dataclass(frozen=True)
class SelectionTotals:
    model_count: int
    unique_buyers: int
    granted_count: int
    live_eligible_count: int


def make_table_row_key(*, brand_normalized: str, model_normalized: str) -> str:
    return f'{brand_normalized}{TABLE_ROW_KEY_SEPARATOR}{model_normalized}'


def build_stats_row_index(rows: list[VehicleStatsRow]) -> dict[str, VehicleStatsRow]:
    return {
        make_table_row_key(
            brand_normalized=row.brand_normalized,
            model_normalized=row.model_normalized,
        ): row
        for row in rows
    }


def validate_table_row_keys(
    raw_keys: list[str],
    row_index: dict[str, VehicleStatsRow],
) -> list[str]:
    validated: list[str] = []
    seen: set[str] = set()
    for raw in raw_keys:
        key = str(raw or '').strip()
        if not key or key in seen:
            continue
        if key not in row_index:
            raise TableSelectionError(f'Недопустимая строка таблицы: {key}.')
        seen.add(key)
        validated.append(key)
    return validated


def build_vehicle_selection_from_table_keys(
    keys: list[str],
    row_index: dict[str, VehicleStatsRow],
) -> list[dict]:
    by_brand: dict[str, dict] = {}
    for key in keys:
        row = row_index[key]
        bucket = by_brand.setdefault(
            row.brand_normalized,
            {
                'brand': row.brand,
                'models': [],
            },
        )
        if row.model not in bucket['models']:
            bucket['models'].append(row.model)
    raw_selection = [
        {
            'brand': bucket['brand'],
            'all_models': False,
            'models': bucket['models'],
        }
        for bucket in by_brand.values()
    ]
    normalized = normalize_vehicle_selection(raw_selection)
    if not normalized:
        raise TableSelectionError('Не удалось сформировать выбор автомобилей.')
    return normalized


def table_selection_to_builder_state(
    vehicle_selection: list[dict],
) -> tuple[set[str], dict[str, bool], dict[str, set[str]]]:
    from core.services.buyer_contact_utils import normalize_buyer_text

    selected_brands: set[str] = set()
    selected_all_models: dict[str, bool] = {}
    selected_models: dict[str, set[str]] = {}
    for entry in vehicle_selection:
        brand_norm = normalize_buyer_text(entry['brand'])
        selected_brands.add(brand_norm)
        selected_all_models[brand_norm] = bool(entry.get('all_models'))
        selected_models[brand_norm] = set(entry.get('models') or [])
    return selected_brands, selected_all_models, selected_models


def compute_selection_totals(
    keys: list[str],
    row_index: dict[str, VehicleStatsRow],
) -> SelectionTotals:
    if not keys:
        return SelectionTotals(0, 0, 0, 0)

    vehicle_q = Q()
    for key in keys:
        row = row_index[key]
        vehicle_q |= Q(
            brand_normalized=row.brand_normalized,
            model_normalized=row.model_normalized,
        )

    queryset = BuyerVehicle.objects.filter(
        buyer__is_test_contact=False,
    ).filter(vehicle_q)
    granted_filter = Q(
        buyer__consents__channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
        buyer__consents__purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        buyer__consents__status=CONTACT_CONSENT_STATUS_GRANTED,
    )
    live_filter = live_eligible_buyer_q(prefix='buyer__')

    return SelectionTotals(
        model_count=len(keys),
        unique_buyers=queryset.values('buyer_id').distinct().count(),
        granted_count=queryset.filter(granted_filter).values('buyer_id').distinct().count(),
        live_eligible_count=queryset.filter(live_filter).values('buyer_id').distinct().count(),
    )
