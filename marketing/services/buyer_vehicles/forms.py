from __future__ import annotations

from core.services.buyer_contact_utils import normalize_buyer_text
from core.services.buyer_vehicle_selection import normalize_vehicle_selection

from marketing.services.audiences.constants import GROUP_BUYERS, SUBTYPE_PARTS_REQUESTS
from marketing.services.audiences.filters import normalize_marketing_criteria
from marketing.services.audiences.validation import CriteriaValidationError, validate_and_normalize_criteria
from marketing.services.buyer_vehicles.stats import BrandModelOption


class BuyerVehicleFormError(ValueError):
    pass


def build_valid_brand_index(tree: list[BrandModelOption]) -> dict[str, BrandModelOption]:
    return {option.brand_normalized: option for option in tree}


def parse_vehicle_selection_from_post(
    post_data,
    *,
    brand_index: dict[str, BrandModelOption],
) -> list[dict]:
    selected_brand_keys = [
        value.strip()
        for value in post_data.getlist('selection_brand')
        if str(value).strip()
    ]
    if not selected_brand_keys:
        raise BuyerVehicleFormError('Выберите хотя бы одну марку или модель.')

    selection: list[dict] = []
    seen_brands: set[str] = set()
    for brand_key in selected_brand_keys:
        if brand_key not in brand_index:
            raise BuyerVehicleFormError(f'Недопустимая марка: {brand_key}.')
        if brand_key in seen_brands:
            continue
        seen_brands.add(brand_key)
        option = brand_index[brand_key]

        all_models_flag = post_data.get(f'selection_all_models__{brand_key}') == '1'
        selected_models = [
            value.strip()
            for value in post_data.getlist(f'selection_model__{brand_key}')
            if str(value).strip()
        ]
        models_map = {
            normalize_buyer_text(model): model
            for model in option.models
        }
        validated_models: list[str] = []
        if not all_models_flag:
            for model in selected_models:
                matched = models_map.get(normalize_buyer_text(model))
                if matched is None:
                    raise BuyerVehicleFormError(
                        f'Недопустимая модель «{model}» для марки «{option.brand}».',
                    )
                if matched not in validated_models:
                    validated_models.append(matched)
            if not validated_models:
                raise BuyerVehicleFormError(
                    f'Выберите модели или «Все модели» для марки «{option.brand}».',
                )
        selection.append({
            'brand': option.brand,
            'all_models': all_models_flag,
            'models': validated_models,
        })

    normalized = normalize_vehicle_selection(selection)
    if not normalized:
        raise BuyerVehicleFormError('Не удалось сформировать выбор автомобилей.')
    return normalized


def parse_extra_filters_from_post(post_data) -> dict:
    return normalize_marketing_criteria(
        {
            'search_cities': [
                value.strip()
                for value in post_data.getlist('search_cities')
                if str(value).strip()
            ],
            'categories': [
                value.strip()
                for value in post_data.getlist('categories')
                if str(value).strip()
            ],
            'category_period': post_data.get('category_period', ''),
            'activity_period': post_data.get('activity_period', ''),
        },
        contact_group=GROUP_BUYERS,
        contact_subtype=SUBTYPE_PARTS_REQUESTS,
    )


def build_audience_criteria(vehicle_selection: list[dict], extra: dict) -> dict:
    raw = {
        'vehicle_selection': vehicle_selection,
        'search_cities': extra.get('search_cities') or [],
        'categories': extra.get('categories') or [],
        'category_period': extra.get('category_period') or '',
        'activity_period': extra.get('activity_period') or '',
    }
    return validate_and_normalize_criteria(
        raw,
        contact_group=GROUP_BUYERS,
        contact_subtype=SUBTYPE_PARTS_REQUESTS,
    )


def validate_audience_criteria(criteria: dict) -> dict:
    try:
        return validate_and_normalize_criteria(
            criteria,
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
        )
    except CriteriaValidationError as exc:
        raise BuyerVehicleFormError(str(exc)) from exc
