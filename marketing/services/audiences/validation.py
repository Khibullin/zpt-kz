from __future__ import annotations

from marketing.services.audiences.builders import subtype_matches_group
from marketing.services.audiences.constants import (
    GROUP_BUYERS,
    GROUP_SELLERS,
    GROUP_SERVICE_PROVIDERS,
    GROUP_TEST,
    SUBTYPE_ALL_BUYERS,
    SUBTYPE_ALL_SELLERS,
    SUBTYPE_ALL_SERVICE_PROVIDERS,
    SUBTYPE_COMBINED_SELLERS,
    SUBTYPE_DETAILING,
    SUBTYPE_MARKETPLACE_PAID,
    SUBTYPE_MARKETPLACE_SELLERS,
    SUBTYPE_PARTS_REQUESTS,
    SUBTYPE_REQUEST_SELLERS,
    SUBTYPE_SERVICE_REQUESTS,
    SUBTYPE_STO,
    SUBTYPE_TEST_CONTACTS,
)
from marketing.services.audiences.filters import normalize_marketing_criteria

MAX_MULTISELECT_VALUES = 50

COMMON_KEYS = frozenset({
    'activity_from',
    'activity_to',
    'activity_period',
    'is_active',
    'is_test',
})

PARTS_BUYER_KEYS = COMMON_KEYS | {
    'countries',
    'primary_cities',
    'search_cities',
    'search_scopes',
    'transport_types',
    'brands',
    'models',
    'categories',
    'category_period',
    'category_source',
    'request_count_min',
    'request_count_max',
}

MARKETPLACE_BUYER_KEYS = COMMON_KEYS | {
    'cities',
    'orders_count_min',
    'orders_count_max',
}

SERVICE_CUSTOMER_KEYS = COMMON_KEYS | {
    'cities',
    'district',
    'service_type',
    'services',
    'brands',
    'models',
}

REQUEST_SELLER_KEYS = COMMON_KEYS | {
    'cities',
    'transport_types',
    'brands',
    'models',
    'categories',
    'receive_requests',
    'is_paused',
}

MARKETPLACE_SELLER_KEYS = COMMON_KEYS | {
    'cities',
    'has_products',
    'has_active_products',
    'products_count_min',
    'products_count_max',
    'has_logo',
    'has_instagram',
    'has_website',
}

SERVICE_PROVIDER_KEYS = COMMON_KEYS | {
    'cities',
    'district',
    'service_type',
    'services',
    'receive_requests',
    'is_paused',
    'has_address',
    'has_map_link',
    'has_logo',
    'has_instagram',
    'has_website',
}

TEST_KEYS = frozenset({'is_active'})

ALLOWED_KEYS_BY_SUBTYPE: dict[tuple[str, str], frozenset[str]] = {
    (GROUP_BUYERS, SUBTYPE_PARTS_REQUESTS): PARTS_BUYER_KEYS,
    (GROUP_BUYERS, SUBTYPE_MARKETPLACE_PAID): MARKETPLACE_BUYER_KEYS,
    (GROUP_BUYERS, SUBTYPE_SERVICE_REQUESTS): SERVICE_CUSTOMER_KEYS,
    (GROUP_BUYERS, SUBTYPE_ALL_BUYERS): COMMON_KEYS | {
        'cities',
        'brands',
        'models',
    },
    (GROUP_SELLERS, SUBTYPE_REQUEST_SELLERS): REQUEST_SELLER_KEYS,
    (GROUP_SELLERS, SUBTYPE_MARKETPLACE_SELLERS): MARKETPLACE_SELLER_KEYS,
    (GROUP_SELLERS, SUBTYPE_COMBINED_SELLERS): REQUEST_SELLER_KEYS | MARKETPLACE_SELLER_KEYS,
    (GROUP_SELLERS, SUBTYPE_ALL_SELLERS): REQUEST_SELLER_KEYS | MARKETPLACE_SELLER_KEYS,
    (GROUP_SERVICE_PROVIDERS, SUBTYPE_STO): SERVICE_PROVIDER_KEYS,
    (GROUP_SERVICE_PROVIDERS, SUBTYPE_DETAILING): SERVICE_PROVIDER_KEYS,
    (GROUP_SERVICE_PROVIDERS, SUBTYPE_ALL_SERVICE_PROVIDERS): SERVICE_PROVIDER_KEYS,
    (GROUP_TEST, SUBTYPE_TEST_CONTACTS): TEST_KEYS,
}


class CriteriaValidationError(ValueError):
    pass


FORM_FIELD_NAMES = frozenset({
    'action',
    'contact_group',
    'contact_subtype',
    'name',
    'description',
    'is_active',
    'step',
    'csrfmiddlewaretoken',
})


def validate_request_post_fields(
    post_data,
    *,
    contact_group: str,
    contact_subtype: str,
) -> None:
    allowed = allowed_criteria_keys(contact_group, contact_subtype) | FORM_FIELD_NAMES
    unknown = set(post_data.keys()) - allowed
    if unknown:
        raise CriteriaValidationError(
            f'Недопустимые поля формы: {", ".join(sorted(unknown))}.',
        )


def allowed_criteria_keys(contact_group: str, contact_subtype: str) -> frozenset[str]:
    if not subtype_matches_group(contact_group, contact_subtype):
        return frozenset()
    return ALLOWED_KEYS_BY_SUBTYPE.get((contact_group, contact_subtype), frozenset())


def _migrate_legacy_criteria_keys(
    raw: dict,
    *,
    contact_group: str,
    contact_subtype: str,
) -> dict:
    migrated = dict(raw)
    if contact_group == GROUP_BUYERS and contact_subtype == SUBTYPE_PARTS_REQUESTS:
        if 'cities' in migrated and 'primary_cities' not in migrated:
            migrated['primary_cities'] = migrated.pop('cities')
        else:
            migrated.pop('cities', None)
    return migrated


def _has_meaningful_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    if isinstance(value, bool):
        return True
    return True


def validate_and_normalize_criteria(
    raw: object,
    *,
    contact_group: str,
    contact_subtype: str,
    reject_unknown: bool = True,
) -> dict:
    if not isinstance(raw, dict):
        raise CriteriaValidationError('Критерии должны быть объектом JSON.')

    raw = _migrate_legacy_criteria_keys(
        raw,
        contact_group=contact_group,
        contact_subtype=contact_subtype,
    )

    allowed = allowed_criteria_keys(contact_group, contact_subtype)
    if not allowed:
        raise CriteriaValidationError('Недопустимая комбинация группы и подтипа аудитории.')

    if reject_unknown:
        unknown = {
            key
            for key in raw.keys()
            if key not in allowed
        }
        if unknown:
            raise CriteriaValidationError(
                f'Недопустимые ключи критериев: {", ".join(sorted(unknown))}.',
            )

    for key, value in raw.items():
        if isinstance(value, list) and len(value) > MAX_MULTISELECT_VALUES:
            raise CriteriaValidationError(
                f'Слишком много значений в «{key}» (максимум {MAX_MULTISELECT_VALUES}).',
            )

    normalized = normalize_marketing_criteria(
        raw,
        contact_group=contact_group,
        contact_subtype=contact_subtype,
    )
    return {key: normalized[key] for key in allowed if key in normalized}


__all__ = [
    'CriteriaValidationError',
    'MAX_MULTISELECT_VALUES',
    'allowed_criteria_keys',
    'validate_and_normalize_criteria',
    'validate_request_post_fields',
]
