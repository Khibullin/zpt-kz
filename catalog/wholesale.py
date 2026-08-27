"""Public wholesale storefront helpers.

Anonymous-visible permanent wholesale prices come from active
ProductPriceTier rows. This module must not change guest retail catalog
pricing or resolve_commercial_price().
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.db.models import Prefetch

from catalog.applicability import extra_compatibility_text, grouped_applicability
from catalog.commercial import check_stock_qty
from catalog.models import Brand, CarModel, Product, ProductPriceTier, SellerProfile


WHOLESALE_TYPE_CABIN = 'cabin_filter'
WHOLESALE_TYPE_OIL = 'oil_filter'
WHOLESALE_TYPE_SPARK = 'spark_plug'

WHOLESALE_TYPE_CHOICES = (
    (WHOLESALE_TYPE_CABIN, 'Салонные фильтры'),
    (WHOLESALE_TYPE_OIL, 'Масляные фильтры'),
    (WHOLESALE_TYPE_SPARK, 'Свечи зажигания'),
)

WHOLESALE_TYPE_LABELS = dict(WHOLESALE_TYPE_CHOICES)

_TYPE_RULES = (
    (WHOLESALE_TYPE_CABIN, ('салонн', 'cabin')),
    (WHOLESALE_TYPE_OIL, ('маслян', 'oil')),
    (WHOLESALE_TYPE_SPARK, ('свеч', 'spark')),
)


def normalize_wholesale_text(value):
    text = ' '.join(str(value or '').strip().lower().replace('\n', ' ').split())
    return text.replace('ё', 'е')


def wholesale_product_type(product):
    """Best-effort type from title/category. Does not change Product schema."""
    haystack = normalize_wholesale_text(
        ' '.join(
            part
            for part in (
                getattr(product, 'title', ''),
                getattr(getattr(product, 'category', None), 'name', ''),
            )
            if part
        )
    )
    if not haystack:
        return ''
    for type_key, tokens in _TYPE_RULES:
        if any(token in haystack for token in tokens):
            return type_key
    return ''


def wholesale_product_type_label(type_key):
    return WHOLESALE_TYPE_LABELS.get(type_key, '')


def public_wholesale_unit_price(product):
    """Permanent public wholesale unit price from an active ProductPriceTier.

    One constant price per product: the active tier with the lowest min_qty.
    Returns None if the product has no active tier.
    """
    cached = getattr(product, '_prefetched_objects_cache', {})
    if 'price_tiers' in cached:
        tiers = [
            tier for tier in product.price_tiers.all()
            if getattr(tier, 'is_active', False)
        ]
        tiers.sort(key=lambda tier: (tier.min_qty, tier.pk or 0))
        return int(tiers[0].price) if tiers else None
    tier = (
        product.price_tiers.filter(is_active=True)
        .order_by('min_qty', 'id')
        .first()
    )
    return int(tier.price) if tier is not None else None


def wholesale_products_qs(seller):
    if seller is None or not seller.wholesale_enabled:
        return Product.objects.none()
    return (
        Product.objects.owned_by_seller(seller)
        .filter(status='active', publish_to_sellers=True)
        .filter(price_tiers__is_active=True)
        .distinct()
        .select_related(
            'brand',
            'car_model',
            'car_model__brand',
            'category',
            'seller_profile',
        )
        .prefetch_related(
            'selected_brands',
            Prefetch(
                'selected_models',
                queryset=CarModel.objects.select_related('brand'),
            ),
            Prefetch(
                'price_tiers',
                queryset=ProductPriceTier.objects.filter(is_active=True).order_by(
                    'min_qty', 'id'
                ),
            ),
        )
    )


def seller_has_wholesale_storefront(seller):
    if seller is None or not seller.wholesale_enabled:
        return False
    return wholesale_products_qs(seller).exists()


def is_wholesale_eligible(product, seller=None):
    if product is None or product.status != 'active' or not product.publish_to_sellers:
        return False
    if public_wholesale_unit_price(product) is None:
        return False
    if seller is None:
        return True
    if not seller.wholesale_enabled:
        return False
    return Product.objects.owned_by_seller(seller).filter(pk=product.pk).exists()


def resolve_wholesale_owner(product):
    if product is None:
        return None
    if product.seller_profile_id:
        return product.seller_profile
    name = (product.seller_name or '').strip()
    if not name:
        return None
    return SellerProfile.objects.filter(name__iexact=name).order_by('pk').first()


@dataclass
class PublicWholesaleQuote:
    quantity: int
    unit_price: int | None
    can_buy: bool
    reason: str = ''
    price_type: str = 'wholesale'
    label: str = 'Опт'

    @property
    def total_price(self):
        if not self.can_buy or self.unit_price is None:
            return None
        return int(self.unit_price) * int(self.quantity)


def quote_public_wholesale(product, quantity, seller=None):
    """Public wholesale quote for guests. Independent of SellerProfile login."""
    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        return PublicWholesaleQuote(0, None, False, 'Укажите количество больше 0.')
    if qty <= 0:
        return PublicWholesaleQuote(qty, None, False, 'Укажите количество больше 0.')

    owner = seller or resolve_wholesale_owner(product)
    if owner is None or not owner.wholesale_enabled:
        return PublicWholesaleQuote(
            qty, None, False, 'Оптовая витрина этого продавца недоступна.'
        )
    if not is_wholesale_eligible(product, owner):
        return PublicWholesaleQuote(
            qty, None, False, 'Этот товар нельзя купить оптом.'
        )

    stock_error = check_stock_qty(product, qty)
    if stock_error:
        return PublicWholesaleQuote(qty, None, False, stock_error)

    unit_price = public_wholesale_unit_price(product)
    if unit_price is None:
        return PublicWholesaleQuote(
            qty, None, False, 'Для товара не задана оптовая цена.'
        )
    return PublicWholesaleQuote(qty, unit_price, True)


def wholesale_fitment_text(product):
    groups = grouped_applicability(product)
    bits = []
    for group in groups:
        brand_name = (group['brand'].name or '').strip()
        models = [
            (model.name or '').strip()
            for model in group['models']
            if (model.name or '').strip()
        ]
        if models:
            bits.append(f'{brand_name} {", ".join(models)}'.strip())
        elif brand_name:
            bits.append(brand_name)
    extra = extra_compatibility_text(product, groups)
    if extra:
        bits.append(re.sub(r'\s+', ' ', extra).strip())
    return '; '.join(bit for bit in bits if bit)


def remaining_wholesale_qty(total_qty, seller):
    minimum = int(getattr(seller, 'wholesale_min_order_qty', 0) or 0)
    if minimum <= 0:
        return 0
    return max(0, minimum - int(total_qty or 0))


def wholesale_car_brands(products_qs):
    """Car brands from primary brand, model brand, and selected_brands."""
    brand_ids = set()
    brand_ids.update(
        products_qs.exclude(brand_id=None).values_list('brand_id', flat=True)
    )
    brand_ids.update(
        products_qs.exclude(car_model__brand_id=None).values_list(
            'car_model__brand_id',
            flat=True,
        )
    )
    brand_ids.update(products_qs.values_list('selected_brands', flat=True))
    brand_ids.discard(None)
    return Brand.objects.filter(pk__in=brand_ids).order_by('name')
