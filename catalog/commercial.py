from dataclasses import dataclass

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
            product.has_b2b_buy_offer = False
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
        product.has_b2b_buy_offer = (
            product.has_wholesale_offer
            or product.has_sale_offer
            or product.has_promo_offer
        )

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


PRICE_RETAIL = 'retail'
PRICE_WHOLESALE = 'wholesale'
PRICE_SALE = 'sale'
PRICE_PROMO = 'promo'

PRICE_TYPE_LABELS = {
    PRICE_RETAIL: 'Розничная цена',
    PRICE_WHOLESALE: 'Оптовая цена',
    PRICE_SALE: 'Распродажа',
    PRICE_PROMO: 'Акция',
}

_PRICE_TYPE_TIEBREAK = {
    PRICE_SALE: 0,
    PRICE_PROMO: 1,
    PRICE_WHOLESALE: 2,
    PRICE_RETAIL: 3,
}

RETAIL_UNAVAILABLE_MESSAGE = (
    'Этот товар доступен только по запросу цены через WhatsApp'
)
B2B_UNAVAILABLE_MESSAGE = (
    'Нет доступной оптовой или акционной цены для покупки этого товара.'
)
QTY_INVALID_MESSAGE = 'Укажите количество больше 0.'
QTY_NEGATIVE_MESSAGE = 'Количество не может быть отрицательным.'


@dataclass
class CommercialQuote:
    quantity: int
    unit_price: int | None
    total_price: int | None
    price_type: str | None
    label: str
    can_buy: bool
    reason: str
    applied_tier_id: int | None = None
    applied_promotion_id: int | None = None

    def to_public_dict(self):
        """JSON-safe payload. Never includes cost_price."""
        return {
            'quantity': self.quantity,
            'unit_price': self.unit_price,
            'total_price': self.total_price,
            'price_type': self.price_type,
            'label': self.label,
            'can_buy': self.can_buy,
            'reason': self.reason,
        }


def _failed_quote(quantity, reason, *, unit_price=None, price_type=None, label=''):
    return CommercialQuote(
        quantity=quantity if isinstance(quantity, int) else 0,
        unit_price=unit_price,
        total_price=None,
        price_type=price_type,
        label=label or '',
        can_buy=False,
        reason=reason,
    )


def _ok_quote(quantity, unit_price, price_type, *, tier=None, promotion=None):
    label = PRICE_TYPE_LABELS[price_type]
    return CommercialQuote(
        quantity=quantity,
        unit_price=int(unit_price),
        total_price=int(unit_price) * quantity,
        price_type=price_type,
        label=label,
        can_buy=True,
        reason='',
        applied_tier_id=getattr(tier, 'id', None),
        applied_promotion_id=getattr(promotion, 'id', None),
    )


def parse_quantity(quantity):
    if isinstance(quantity, bool) or quantity is None:
        return None
    try:
        return int(quantity)
    except (TypeError, ValueError):
        return None


def check_stock_qty(product, quantity):
    """Return an error message if quantity exceeds stock. None means unlimited."""
    stock_qty = getattr(product, 'stock_qty', None)
    if stock_qty is None:
        return ''
    if quantity > stock_qty:
        return (
            f'Недостаточно товара на складе. Доступно: {stock_qty} шт.'
        )
    return ''


def _active_tiers(product):
    cached = getattr(product, 'visible_price_tiers', None)
    if cached is not None:
        return [tier for tier in cached if tier.is_active]
    return [
        tier for tier in product.price_tiers.all()
        if tier.is_active
    ]


def _best_wholesale_tier(tiers, quantity):
    eligible = [tier for tier in tiers if tier.min_qty <= quantity]
    if not eligible:
        return None
    return max(eligible, key=lambda tier: (tier.min_qty, tier.id))


def _active_promotions(product, now):
    cached = getattr(product, 'visible_promotions', None)
    if cached is not None:
        return [
            promo for promo in cached
            if promotion_is_currently_active(promo, now=now)
        ]
    return [
        promo for promo in product.promotions.all()
        if promotion_is_currently_active(promo, now=now)
    ]


def _promotion_applies(promotion, quantity):
    if promotion.qty_limit is None:
        return True
    return quantity <= promotion.qty_limit


def resolve_commercial_price(product, quantity, seller_profile=None, now=None):
    """Single source of truth for retail / wholesale / sale / promo unit price.

    Guests and users without SellerProfile always get Product.price.
    SellerProfile gets the lowest currently available unit price.
    Never writes back to Product.price. Never exposes cost_price.
    """
    qty = parse_quantity(quantity)
    if qty is None:
        return _failed_quote(0, QTY_INVALID_MESSAGE)
    if qty < 0:
        return _failed_quote(qty, QTY_NEGATIVE_MESSAGE)
    if qty == 0:
        return _failed_quote(qty, QTY_INVALID_MESSAGE)

    stock_error = check_stock_qty(product, qty)
    if stock_error:
        return _failed_quote(qty, stock_error)

    now = timezone.now() if now is None else now
    retail_price = product.price
    has_retail = (
        not product.price_on_request
        and retail_price is not None
    )

    if seller_profile is None:
        if not has_retail:
            return _failed_quote(qty, RETAIL_UNAVAILABLE_MESSAGE)
        return _ok_quote(qty, retail_price, PRICE_RETAIL)

    candidates = []
    if has_retail:
        candidates.append({
            'unit_price': int(retail_price),
            'price_type': PRICE_RETAIL,
            'tier': None,
            'promotion': None,
        })

    wholesale_tier = _best_wholesale_tier(_active_tiers(product), qty)
    if wholesale_tier is not None:
        candidates.append({
            'unit_price': int(wholesale_tier.price),
            'price_type': PRICE_WHOLESALE,
            'tier': wholesale_tier,
            'promotion': None,
        })

    for promotion in _active_promotions(product, now):
        if not _promotion_applies(promotion, qty):
            continue
        if promotion.promotion_type == ProductPromotion.TYPE_SALE:
            price_type = PRICE_SALE
        elif promotion.promotion_type == ProductPromotion.TYPE_PROMO:
            price_type = PRICE_PROMO
        else:
            continue
        candidates.append({
            'unit_price': int(promotion.price),
            'price_type': price_type,
            'tier': None,
            'promotion': promotion,
        })

    if not candidates:
        reason = (
            B2B_UNAVAILABLE_MESSAGE
            if product.price_on_request
            else RETAIL_UNAVAILABLE_MESSAGE
        )
        return _failed_quote(qty, reason)

    chosen = min(
        candidates,
        key=lambda item: (
            item['unit_price'],
            _PRICE_TYPE_TIEBREAK.get(item['price_type'], 9),
        ),
    )
    return _ok_quote(
        qty,
        chosen['unit_price'],
        chosen['price_type'],
        tier=chosen['tier'],
        promotion=chosen['promotion'],
    )


def validate_consignment_request(product, seller_profile, quantity):
    """Return (ok, error_message, consignment). Does not decrement stock."""
    if seller_profile is None:
        return False, 'Подать заявку на реализацию может только зарегистрированный продавец.', None

    qty = parse_quantity(quantity)
    if qty is None or qty == 0:
        return False, QTY_INVALID_MESSAGE, None
    if qty < 0:
        return False, QTY_NEGATIVE_MESSAGE, None

    consignment = _safe_consignment(product)
    if consignment is None or not consignment.enabled:
        return False, 'Этот товар сейчас нельзя взять на реализацию.', None

    if consignment.max_qty:
        if qty > consignment.max_qty:
            return (
                False,
                f'Максимум для реализации: {consignment.max_qty} шт.',
                consignment,
            )

    stock_error = check_stock_qty(product, qty)
    if stock_error:
        return False, stock_error, consignment

    return True, '', consignment
