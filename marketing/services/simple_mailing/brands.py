from __future__ import annotations

from django.db.models import Q

from core.models import Request, Seller
from core.services.buyer_contact_utils import normalize_buyer_text
from marketing.services.simple_mailing.constants import (
    MARKETPLACE_BRAND_FILTER_AVAILABLE,
    RECIPIENT_TYPE_MARKETPLACE_BUYERS,
    RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
    RECIPIENT_TYPE_SELLERS,
    TEST_BRAND_RAW_NAMES,
)

_TEST_BRAND_KEYS = frozenset(
    normalize_buyer_text(name)
    for name in TEST_BRAND_RAW_NAMES
)


class SimpleMailingValidationError(ValueError):
    pass


def is_test_brand_value(brand: object) -> bool:
    key = normalize_buyer_text(brand)
    return bool(key) and key in _TEST_BRAND_KEYS


def build_exclude_test_brand_q(*, field_prefix: str = '') -> Q:
    prefix = f'{field_prefix}__' if field_prefix else ''
    exclude = Q()
    for brand in TEST_BRAND_RAW_NAMES:
        exclude |= Q(**{f'{prefix}brand__iexact': brand})
    return exclude


def normalize_brand_selection(
    *,
    all_brands: bool,
    brands: list[str],
) -> tuple[bool, list[str]]:
    if all_brands:
        return True, []
    return False, list(brands)


def has_brand_selection(
    *,
    recipient_type: str,
    all_brands: bool,
    brands: list[str],
) -> bool:
    if recipient_type == RECIPIENT_TYPE_MARKETPLACE_BUYERS and not MARKETPLACE_BRAND_FILTER_AVAILABLE:
        return True
    return all_brands or bool(brands)


def _sorted_unique_brands(values) -> list[str]:
    seen: set[str] = set()
    brands: list[str] = []
    for value in values:
        text = str(value or '').strip()
        if not text or is_test_brand_value(text):
            continue
        key = normalize_buyer_text(text)
        if not key or key in seen:
            continue
        seen.add(key)
        brands.append(text)
    return sorted(brands, key=lambda item: item.casefold())


def get_available_brands(recipient_type: str) -> list[str]:
    if recipient_type == RECIPIENT_TYPE_PARTS_REQUEST_BUYERS:
        raw = (
            Request.objects.filter(
                buyer_contact__isnull=False,
                buyer_contact__is_test_contact=False,
            )
            .exclude(brand='')
            .values_list('brand', flat=True)
            .distinct()
        )
        return _sorted_unique_brands(raw)

    if recipient_type == RECIPIENT_TYPE_SELLERS:
        legacy = (
            Seller.objects.filter(is_active=True, is_test_seller=False)
            .exclude(brand='')
            .values_list('brand', flat=True)
            .distinct()
        )
        fk_names = (
            Seller.objects.filter(is_active=True, is_test_seller=False)
            .exclude(brand_fk__isnull=True)
            .values_list('brand_fk__name', flat=True)
            .distinct()
        )
        m2m_names = (
            Seller.objects.filter(is_active=True, is_test_seller=False)
            .values_list('selected_brands__name', flat=True)
            .distinct()
        )
        return _sorted_unique_brands(list(legacy) + list(fk_names) + list(m2m_names))

    if recipient_type == RECIPIENT_TYPE_MARKETPLACE_BUYERS:
        if not MARKETPLACE_BRAND_FILTER_AVAILABLE:
            return []
        from catalog.models import Product
        from orders.models import Order

        product_ids = (
            Order.objects.filter(status=Order.STATUS_PAID)
            .values_list('items__product_id', flat=True)
            .distinct()
        )
        raw = (
            Product.objects.filter(pk__in=product_ids)
            .exclude(brand__isnull=True)
            .values_list('brand__name', flat=True)
            .distinct()
        )
        return _sorted_unique_brands(raw)

    return []


def marketplace_brand_filter_enabled(recipient_type: str) -> bool:
    if recipient_type != RECIPIENT_TYPE_MARKETPLACE_BUYERS:
        return True
    return MARKETPLACE_BRAND_FILTER_AVAILABLE


def validate_brand_selection(
    *,
    recipient_type: str,
    all_brands: bool,
    brands: list[str],
) -> list[str]:
    all_brands, brands = normalize_brand_selection(all_brands=all_brands, brands=brands)

    if all_brands:
        return []

    if not brands:
        raise SimpleMailingValidationError('Выберите «Все марки» или одну и более марок.')

    if recipient_type == RECIPIENT_TYPE_MARKETPLACE_BUYERS and not MARKETPLACE_BRAND_FILTER_AVAILABLE:
        raise SimpleMailingValidationError(
            'Для Marketplace фильтрация по марке пока недоступна. Выберите «Все марки».',
        )

    allowed = {
        normalize_buyer_text(brand): brand
        for brand in get_available_brands(recipient_type)
    }
    validated: list[str] = []
    seen: set[str] = set()
    for brand in brands:
        key = normalize_buyer_text(brand)
        if not key or key in seen:
            continue
        if is_test_brand_value(brand):
            raise SimpleMailingValidationError(f'Недопустимая марка: {brand}.')
        matched = allowed.get(key)
        if matched is None:
            raise SimpleMailingValidationError(f'Недопустимая марка: {brand}.')
        seen.add(key)
        validated.append(matched)
    if not validated:
        raise SimpleMailingValidationError('Выберите «Все марки» или одну и более марок.')
    return validated


def build_request_brand_filter_q(brands: list[str]) -> Q:
    brand_q = Q()
    for brand in brands:
        brand_q |= Q(brand__iexact=brand)
    return brand_q


def build_seller_brand_filter_q(brands: list[str]) -> Q:
    brand_q = Q(all_brands=True)
    for brand in brands:
        brand_q |= Q(brand__iexact=brand)
        brand_q |= Q(brand_fk__name__iexact=brand)
        brand_q |= Q(selected_brands__name__iexact=brand)
    return brand_q
