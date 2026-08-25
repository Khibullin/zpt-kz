from django.db.models import Prefetch, Q
from django.utils import timezone

from .models import (
    CarModel,
    ProductConsignment,
    ProductPriceTier,
    ProductPromotion,
    SellerProfile,
)

OFFER_WHOLESALE = 'wholesale'
OFFER_SALE = 'sale'
OFFER_PROMO = 'promo'
OFFER_CONSIGNMENT = 'consignment'

OFFER_CHOICES = (
    ('', 'Все'),
    (OFFER_WHOLESALE, 'Опт'),
    (OFFER_SALE, 'Распродажа'),
    (OFFER_PROMO, 'Акции'),
    (OFFER_CONSIGNMENT, 'На реализацию'),
)

VALID_OFFER_VALUES = {
    OFFER_WHOLESALE,
    OFFER_SALE,
    OFFER_PROMO,
    OFFER_CONSIGNMENT,
}


def get_request_seller_profile(request):
    """Return SellerProfile for the logged-in user, or None.

    Never raises if the user is anonymous or has no profile.
    """
    user = getattr(request, 'user', None)
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    user_id = getattr(user, 'pk', None) or getattr(user, 'id', None)
    if not user_id:
        return None
    try:
        return SellerProfile.objects.filter(user_id=user_id).first()
    except Exception:
        return None


def active_promotion_q(prefix='', now=None):
    """Timezone-aware Q() for a currently running ProductPromotion."""
    now = timezone.now() if now is None else now

    def field(name):
        return f'{prefix}__{name}' if prefix else name

    starts = field('starts_at')
    ends = field('ends_at')
    return (
        Q(**{field('is_active'): True})
        & (Q(**{f'{starts}__isnull': True}) | Q(**{f'{starts}__lte': now}))
        & (Q(**{f'{ends}__isnull': True}) | Q(**{f'{ends}__gte': now}))
    )


def promotion_is_currently_active(promotion, now=None):
    if promotion is None or not promotion.is_active:
        return False
    now = timezone.now() if now is None else now
    if promotion.starts_at is not None and promotion.starts_at > now:
        return False
    if promotion.ends_at is not None and promotion.ends_at < now:
        return False
    return True


def filter_products_by_vehicle(products, *, country_id='', brand_id='', model_id=''):
    """Match primary brand/model or extra selected_brands / selected_models."""
    needs_distinct = False

    if country_id:
        products = products.filter(
            Q(brand__country_id=country_id)
            | Q(selected_brands__country_id=country_id)
            | Q(selected_models__brand__country_id=country_id)
        )
        needs_distinct = True

    if brand_id:
        products = products.filter(
            Q(brand_id=brand_id)
            | Q(selected_brands__id=brand_id)
            | Q(selected_models__brand_id=brand_id)
        )
        needs_distinct = True

    if model_id:
        products = products.filter(
            Q(car_model_id=model_id)
            | Q(selected_models__id=model_id)
        )
        needs_distinct = True

    if needs_distinct:
        products = products.distinct()
    return products


def filter_products_by_offer(products, offer, *, now=None):
    if offer == OFFER_WHOLESALE:
        products = products.filter(price_tiers__is_active=True)
    elif offer == OFFER_SALE:
        products = products.filter(
            active_promotion_q(prefix='promotions', now=now),
            promotions__promotion_type=ProductPromotion.TYPE_SALE,
        )
    elif offer == OFFER_PROMO:
        products = products.filter(
            active_promotion_q(prefix='promotions', now=now),
            promotions__promotion_type=ProductPromotion.TYPE_PROMO,
        )
    elif offer == OFFER_CONSIGNMENT:
        products = products.filter(consignment__enabled=True)
    else:
        return products
    return products.distinct()


def b2b_prefetch(queryset, *, now=None):
    """Prefetch commercial offers for seller catalog/detail views."""
    now = timezone.now() if now is None else now
    return queryset.select_related('consignment').prefetch_related(
        Prefetch(
            'price_tiers',
            queryset=ProductPriceTier.objects.filter(
                is_active=True,
            ).order_by('min_qty', 'id'),
            to_attr='visible_price_tiers',
        ),
        Prefetch(
            'promotions',
            queryset=ProductPromotion.objects.filter(
                active_promotion_q(now=now),
            ).order_by('promotion_type', 'id'),
            to_attr='visible_promotions',
        ),
    )


def _safe_consignment(product):
    try:
        return product.consignment
    except ProductConsignment.DoesNotExist:
        return None
    except AttributeError:
        return None


def attach_b2b_offers(products, *, enabled):
    """Set display attributes used by catalog/detail templates. No extra queries."""
    if not enabled:
        for product in products:
            product.visible_price_tiers = []
            product.visible_sale = None
            product.visible_promo = None
            product.visible_consignment = None
            product.has_wholesale_offer = False
            product.has_sale_offer = False
            product.has_promo_offer = False
            product.has_consignment_offer = False
        return products

    for product in products:
        tiers = getattr(product, 'visible_price_tiers', None)
        if tiers is None:
            tiers = [
                tier for tier in product.price_tiers.all()
                if tier.is_active
            ]
        product.visible_price_tiers = list(tiers)

        promotions = getattr(product, 'visible_promotions', None)
        if promotions is None:
            promotions = [
                promo for promo in product.promotions.all()
                if promotion_is_currently_active(promo)
            ]

        sale = None
        promo = None
        for item in promotions:
            if sale is None and item.promotion_type == ProductPromotion.TYPE_SALE:
                sale = item
            elif promo is None and item.promotion_type == ProductPromotion.TYPE_PROMO:
                promo = item

        consignment = _safe_consignment(product)
        if consignment is not None and not consignment.enabled:
            consignment = None

        product.visible_sale = sale
        product.visible_promo = promo
        product.visible_consignment = consignment
        product.has_wholesale_offer = bool(product.visible_price_tiers)
        product.has_sale_offer = sale is not None
        product.has_promo_offer = promo is not None
        product.has_consignment_offer = consignment is not None

    return products


def additional_fitment_models(product):
    """Extra selected_models, skipping the primary car_model. No duplicates."""
    extras = []
    seen = set()
    primary_id = product.car_model_id
    if primary_id:
        seen.add(primary_id)

    for model in product.selected_models.all():
        if model.id in seen:
            continue
        seen.add(model.id)
        extras.append(model)
    return extras


def build_catalog_query(request_get, **overrides):
    params = request_get.copy()
    for key, value in overrides.items():
        if value in (None, ''):
            params.pop(key, None)
        else:
            params[key] = value
    return params.urlencode()
