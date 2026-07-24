from __future__ import annotations

from django.db.models import Q, QuerySet

from core.models import BuyerContact
from core.services.buyer_contact_utils import normalize_buyer_text

MAX_VEHICLE_SELECTION_BRANDS = 50
MAX_VEHICLE_SELECTION_MODELS = 50


def normalize_vehicle_selection(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    normalized: list[dict] = []
    seen_brands: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        brand = str(entry.get('brand') or '').strip()
        if not brand:
            continue
        brand_norm = normalize_buyer_text(brand)
        if not brand_norm or brand_norm in seen_brands:
            continue
        seen_brands.add(brand_norm)
        all_models = bool(entry.get('all_models'))
        models_raw = entry.get('models') or []
        models: list[str] = []
        if not all_models and isinstance(models_raw, list):
            seen_models: set[str] = set()
            for item in models_raw:
                model = str(item or '').strip()
                if not model:
                    continue
                model_norm = normalize_buyer_text(model)
                if not model_norm or model_norm in seen_models:
                    continue
                seen_models.add(model_norm)
                models.append(model)
                if len(models) >= MAX_VEHICLE_SELECTION_MODELS:
                    break
        if not all_models and not models:
            continue
        normalized.append({
            'brand': brand,
            'brand_normalized': brand_norm,
            'all_models': all_models,
            'models': models,
        })
        if len(normalized) >= MAX_VEHICLE_SELECTION_BRANDS:
            break
    return normalized


def vehicle_selection_has_filters(selection: list[dict]) -> bool:
    return bool(selection)


def build_vehicle_selection_filter_q(selection: list[dict]) -> Q:
    combined = Q()
    for entry in selection:
        brand_norm = entry['brand_normalized']
        if entry.get('all_models') or not entry.get('models'):
            combined |= Q(vehicles__brand_normalized=brand_norm)
            continue
        model_norms = [normalize_buyer_text(model) for model in entry['models']]
        for model_norm in model_norms:
            if model_norm:
                combined |= Q(
                    vehicles__brand_normalized=brand_norm,
                    vehicles__model_normalized=model_norm,
                )
    return combined


def apply_vehicle_selection(
    queryset: QuerySet[BuyerContact],
    selection: list[dict],
) -> QuerySet[BuyerContact]:
    if not selection:
        return queryset
    return queryset.filter(build_vehicle_selection_filter_q(selection)).distinct()


def flatten_vehicle_selection_brands_models(selection: list[dict]) -> tuple[list[str], list[str]]:
    brands: list[str] = []
    models: list[str] = []
    seen_brands: set[str] = set()
    seen_models: set[str] = set()
    for entry in selection:
        brand = entry.get('brand') or ''
        brand_norm = entry.get('brand_normalized') or normalize_buyer_text(brand)
        if brand_norm and brand_norm not in seen_brands:
            seen_brands.add(brand_norm)
            brands.append(brand)
        if entry.get('all_models'):
            continue
        for model in entry.get('models') or []:
            model_norm = normalize_buyer_text(model)
            if model_norm and model_norm not in seen_models:
                seen_models.add(model_norm)
                models.append(model)
    return brands, models
