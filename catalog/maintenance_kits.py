"""Maintenance kit pricing, stock and atomic cart add.

Does not store kit price or kit stock. Does not touch PP1/PP2.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from django.db import transaction

from catalog.commercial import get_request_seller_profile, resolve_commercial_price
from catalog.models import MaintenanceKit, MaintenanceKitItem, Product
from orders.cart import CartManager
from orders.constants import CART_MODE_RETAIL, CART_MODE_WHOLESALE, SESSION_CART_KEY, SESSION_CART_MODE_KEY
from orders.seller_utils import CartModeConflictError, CartSellerConflictError, validate_product_for_cart


KIT_UNAVAILABLE_MESSAGE = 'Этот комплект сейчас нельзя добавить в корзину.'
KIT_EMPTY_MESSAGE = 'В комплекте нет позиций.'
KIT_INACTIVE_MESSAGE = 'Комплект не опубликован.'
KIT_INTERNAL_SELLER_MESSAGE = (
    'Комплект нельзя добавить: его позиции относятся к разным продавцам.'
)
STOCK_UNLIMITED_LABEL = 'Наличие уточняется'


class MaintenanceKitCartError(ValueError):
    """Kit cannot be added; cart must stay unchanged."""


@dataclass
class KitLineView:
    item: MaintenanceKitItem
    product: Product
    quantity: int
    unit_price: int | None
    subtotal: int | None
    can_buy: bool
    reason: str
    quote: object


@dataclass
class KitView:
    kit: MaintenanceKit
    lines: list[KitLineView]
    total_price: int | None
    available_kits: int | None
    can_add: bool
    reason: str


def kit_years_display(kit: MaintenanceKit) -> str:
    year_from = kit.year_from
    year_to = kit.year_to
    if year_from and year_to:
        if year_from == year_to:
            return str(year_from)
        return f'{year_from}–{year_to}'
    if year_from:
        return f'с {year_from}'
    if year_to:
        return f'до {year_to}'
    return ''


def available_kits(kit: MaintenanceKit) -> int | None:
    """MIN(stock_qty // required) over numeric stock_qty only.

    None stock does not constrain. All-None → None. Zero or insufficient → 0.
    """
    limits = []
    saw_numeric = False
    items = list(kit.items.all())
    if not items:
        return 0
    for item in items:
        product = item.product
        required = int(item.quantity)
        stock = getattr(product, 'stock_qty', None)
        if stock is None:
            continue
        saw_numeric = True
        stock = int(stock)
        if required < 1 or stock < required:
            return 0
        limits.append(stock // required)
    if not saw_numeric:
        return None
    return min(limits) if limits else 0


def _quote_commercial(product, quantity, seller_profile, *, enforce_stock=True):
    original = getattr(product, 'stock_qty', None)
    if not enforce_stock:
        product.stock_qty = None
    try:
        return resolve_commercial_price(
            product,
            quantity,
            seller_profile=seller_profile,
        )
    finally:
        product.stock_qty = original


def quote_kit_lines(kit: MaintenanceKit, seller_profile=None, *, enforce_stock=True):
    lines = []
    for item in kit.items.all():
        product = item.product
        quantity = int(item.quantity)
        quote = _quote_commercial(
            product,
            quantity,
            seller_profile,
            enforce_stock=enforce_stock,
        )
        lines.append(KitLineView(
            item=item,
            product=product,
            quantity=quantity,
            unit_price=quote.unit_price,
            subtotal=quote.total_price,
            can_buy=bool(quote.can_buy),
            reason=quote.reason or '',
            quote=quote,
        ))
    return lines


def build_kit_view(kit: MaintenanceKit, request=None) -> KitView:
    seller_profile = get_request_seller_profile(request) if request is not None else None
    display_lines = quote_kit_lines(kit, seller_profile, enforce_stock=False)
    totals = [line.subtotal for line in display_lines]
    total_price = sum(totals) if totals and all(value is not None for value in totals) else None
    available = available_kits(kit)
    reason = ''
    can_add = True
    try:
        if request is not None:
            preflight_add_kit(request, kit)
        else:
            _preflight_kit_contents(kit)
            if available == 0:
                raise MaintenanceKitCartError(KIT_UNAVAILABLE_MESSAGE)
    except (MaintenanceKitCartError, CartSellerConflictError, CartModeConflictError, ValueError) as exc:
        can_add = False
        if isinstance(exc, CartSellerConflictError):
            reason = (
                f'В корзине уже есть товары продавца «{exc.seller_name}». '
                'Сначала оформите текущий заказ или очистите корзину.'
            )
        else:
            reason = str(exc) or KIT_UNAVAILABLE_MESSAGE
    return KitView(
        kit=kit,
        lines=display_lines,
        total_price=total_price,
        available_kits=available,
        can_add=can_add,
        reason=reason,
    )


def guest_kit_base_price(kit: MaintenanceKit) -> int | None:
    """Admin-facing guest commercial total. Same resolver, stock not enforced."""
    lines = quote_kit_lines(kit, seller_profile=None, enforce_stock=False)
    totals = [line.subtotal for line in lines]
    if not totals or any(value is None for value in totals):
        return None
    return sum(totals)


def _preflight_kit_contents(kit: MaintenanceKit):
    if not kit.is_active:
        raise MaintenanceKitCartError(KIT_INACTIVE_MESSAGE)
    items = list(kit.items.all())
    if not items:
        raise MaintenanceKitCartError(KIT_EMPTY_MESSAGE)

    synthetic = []
    for item in items:
        quantity = int(item.quantity)
        if quantity < 1:
            raise MaintenanceKitCartError('Количество в комплекте должно быть не меньше 1.')
        product = item.product
        if product is None or product.pk is None:
            raise MaintenanceKitCartError(KIT_UNAVAILABLE_MESSAGE)
        if getattr(product, 'status', '') != 'active':
            raise MaintenanceKitCartError(
                f'Товар «{product.title}» недоступен для покупки.'
            )
        try:
            validate_product_for_cart(synthetic, product)
        except CartSellerConflictError:
            raise MaintenanceKitCartError(KIT_INTERNAL_SELLER_MESSAGE)
        synthetic.append({'product': product, 'quantity': quantity})
    return items


def preflight_add_kit(request, kit: MaintenanceKit):
    items = _preflight_kit_contents(kit)
    cart = CartManager(request)
    if cart.get_mode() == CART_MODE_WHOLESALE:
        raise CartModeConflictError(cart._mode_conflict_message(CART_MODE_RETAIL))

    existing_items = cart.get_items()
    quantities = cart.get_product_quantities()
    seller_profile = cart._seller_profile()

    for item in items:
        product = item.product
        validate_product_for_cart(existing_items, product)
        requested = int(item.quantity)
        resulting = quantities.get(product.pk, 0) + requested
        quote = resolve_commercial_price(
            product,
            resulting,
            seller_profile=seller_profile,
        )
        if not quote.can_buy:
            raise MaintenanceKitCartError(
                quote.reason or f'Товар «{product.title}» нельзя добавить в корзину.'
            )
    return items, cart


def add_kit_to_cart(request, kit: MaintenanceKit):
    """Add every kit component or leave the cart unchanged."""
    items, cart = preflight_add_kit(request, kit)

    if cart.user:
        with transaction.atomic():
            for item in items:
                cart.add(
                    product_id=item.product_id,
                    quantity=item.quantity,
                    accumulate=True,
                    mode=CART_MODE_RETAIL,
                )
        return cart

    snapshot = deepcopy(request.session.get(SESSION_CART_KEY, {}))
    mode_snapshot = request.session.get(SESSION_CART_MODE_KEY)
    try:
        for item in items:
            cart.add(
                product_id=item.product_id,
                quantity=item.quantity,
                accumulate=True,
                mode=CART_MODE_RETAIL,
            )
    except Exception:
        request.session[SESSION_CART_KEY] = snapshot
        if mode_snapshot:
            request.session[SESSION_CART_MODE_KEY] = mode_snapshot
        else:
            request.session.pop(SESSION_CART_MODE_KEY, None)
        request.session.modified = True
        raise
    return cart


def published_kits_queryset():
    return (
        MaintenanceKit.objects.filter(is_active=True)
        .select_related('brand', 'car_model', 'car_model__brand')
        .prefetch_related(
            'items__product',
        )
    )
