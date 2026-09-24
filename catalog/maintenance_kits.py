"""Maintenance kit pricing, stock and atomic cart add.

Does not store kit price or kit stock. Does not touch PP1/PP2.
Orderability uses the same commercial quote as the rest of ZPT.KZ retail cart:
Product.status, Product.price / price_on_request, and Product.stock_qty.
PP1 is a planning reserve and is never added to live availability.

Buyer-facing stock copy:
- Product.status=active, resolve_commercial_price.can_buy, and numeric
  Product.stock_qty covering the line qty → «В наличии» (confirmed remainder).
- can_buy with stock_qty is None → «Можно заказать» (orderable, remainder not counted).
- otherwise → «Нет в наличии» or «Артикул не подтверждён».
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from django.db import transaction

from catalog.commercial import get_request_seller_profile, resolve_commercial_price
from catalog.models import MaintenanceKit, MaintenanceKitItem, Product
from catalog.wholesale import (
    WHOLESALE_TYPE_AIR,
    WHOLESALE_TYPE_CABIN,
    WHOLESALE_TYPE_OIL,
    WHOLESALE_TYPE_SPARK,
    wholesale_product_type,
)
from orders.cart import CartManager
from orders.constants import CART_MODE_RETAIL, CART_MODE_WHOLESALE, SESSION_CART_KEY, SESSION_CART_MODE_KEY
from orders.seller_utils import CartModeConflictError, CartSellerConflictError, validate_product_for_cart


KIT_UNAVAILABLE_MESSAGE = 'Этот комплект сейчас нельзя добавить в корзину.'
KIT_EMPTY_MESSAGE = 'В комплекте нет позиций.'
KIT_EMPTY_SELECTION_MESSAGE = 'Выберите хотя бы одну позицию.'
KIT_INVALID_SELECTION_MESSAGE = 'Выбранная позиция не входит в этот комплект.'
KIT_LINE_UNAVAILABLE_MESSAGE = 'Выбранную позицию сейчас нельзя заказать.'
KIT_INACTIVE_MESSAGE = 'Комплект не опубликован.'
KIT_INTERNAL_SELLER_MESSAGE = (
    'Комплект нельзя добавить: его позиции относятся к разным продавцам.'
)
STOCK_UNLIMITED_LABEL = 'Наличие уточняется'
COVER_PARTIAL_CAPTION = (
    'На фото полный комплект; выбранный состав указан ниже'
)
OEM_UNKNOWN_LABEL = 'OEM неизвестен'
ARTICLE_PENDING_LABEL = 'Артикул уточняется'
PARTS_REQUEST_URL = '/request-parts/'

LINE_AVAILABLE = 'available'
LINE_UNAVAILABLE = 'unavailable'
LINE_UNCONFIRMED = 'unconfirmed'

KIT_COMPONENT_TYPE_LABELS = {
    WHOLESALE_TYPE_CABIN: 'Салонный фильтр',
    WHOLESALE_TYPE_OIL: 'Масляный фильтр',
    WHOLESALE_TYPE_AIR: 'Воздушный фильтр',
    WHOLESALE_TYPE_SPARK: 'Свеча зажигания',
}

KIT_ARTICLE_TYPES = {
    'T151109111': WHOLESALE_TYPE_AIR,
    'T218107011': WHOLESALE_TYPE_CABIN,
    '4801012010': WHOLESALE_TYPE_OIL,
    'F4J163707010': WHOLESALE_TYPE_SPARK,
    '151000025AA': WHOLESALE_TYPE_AIR,
    '301001199AA': WHOLESALE_TYPE_CABIN,
    'F4J161012030': WHOLESALE_TYPE_OIL,
    '1109190CR01': WHOLESALE_TYPE_AIR,
    'CD569F2801032700': WHOLESALE_TYPE_CABIN,
    'D20T0120700': WHOLESALE_TYPE_SPARK,
    'S3010140903': WHOLESALE_TYPE_AIR,
    'C281F2801032601': WHOLESALE_TYPE_CABIN,
    '1109101XGW01A': WHOLESALE_TYPE_AIR,
    '1017110XEN01': WHOLESALE_TYPE_OIL,
}


def kit_component_type_label(product) -> str:
    """Buyer-facing part type. Does not use Product.title (may name another car)."""
    type_key = wholesale_product_type(product)
    if type_key in KIT_COMPONENT_TYPE_LABELS:
        return KIT_COMPONENT_TYPE_LABELS[type_key]
    article = str(getattr(product, 'article', '') or '').strip()
    return KIT_COMPONENT_TYPE_LABELS.get(KIT_ARTICLE_TYPES.get(article, ''), '')


def kit_component_display_name(product) -> str:
    type_label = kit_component_type_label(product)
    article = str(getattr(product, 'article', '') or '').strip()
    if type_label and article:
        return f'{type_label} — {article}'
    if type_label:
        return type_label
    if article:
        return article
    return 'Расходник'


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
    display_name: str
    availability: str
    availability_label: str
    selected_by_default: bool
    request_url: str


@dataclass
class KitReferenceLineView:
    type_label: str
    article: str
    article_display: str
    note: str
    display_name: str


@dataclass
class KitView:
    kit: MaintenanceKit
    lines: list[KitLineView]
    reference_lines: list[KitReferenceLineView]
    total_price: int | None
    full_total_price: int | None
    available_kits: int | None
    can_add: bool
    reason: str
    has_unavailable: bool
    has_unconfirmed: bool
    orderable_count: int


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
    """How many *complete* kits can be assembled from Product.stock_qty.

    None stock does not constrain. All-None → None. Zero or insufficient → 0.
    Does not hide a published car from the picker. Does not read PP1/PP2.
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


def classify_kit_line(product, purchase_quote) -> str:
    """Separate unconfirmed article from 'in catalog but not orderable now'."""
    if product is None or getattr(product, 'pk', None) is None:
        return LINE_UNCONFIRMED
    article = str(getattr(product, 'article', '') or '').strip()
    if not article:
        return LINE_UNCONFIRMED
    if getattr(product, 'status', '') != 'active':
        return LINE_UNAVAILABLE
    if purchase_quote is not None and purchase_quote.can_buy:
        return LINE_AVAILABLE
    return LINE_UNAVAILABLE


def _availability_label(availability: str, product=None) -> str:
    if availability == LINE_AVAILABLE:
        stock = getattr(product, 'stock_qty', None)
        if stock is not None:
            return 'В наличии'
        return 'Можно заказать'
    if availability == LINE_UNCONFIRMED:
        return 'Артикул не подтверждён'
    return 'Нет в наличии'


def _line_request_url(availability: str) -> str:
    if availability == LINE_UNAVAILABLE:
        return PARTS_REQUEST_URL
    return ''


def kit_reference_line_views(kit: MaintenanceKit) -> list[KitReferenceLineView]:
    """Missing/disputed rows. Never priced and never added to the cart."""
    rows = []
    for raw in kit.reference_lines or []:
        if not isinstance(raw, dict):
            continue
        type_label = str(raw.get('type_label') or '').strip() or 'Расходник'
        article = str(raw.get('article') or '').strip()
        if article:
            article_display = article
        else:
            article_display = OEM_UNKNOWN_LABEL
        note = str(raw.get('note') or '').strip()
        rows.append(KitReferenceLineView(
            type_label=type_label,
            article=article,
            article_display=article_display,
            note=note,
            display_name=f'{type_label} — {article_display}',
        ))
    return rows


def quote_kit_lines(kit: MaintenanceKit, seller_profile=None, *, enforce_stock=True):
    lines = []
    for item in kit.items.all():
        product = item.product
        quantity = int(item.quantity)
        display_quote = _quote_commercial(
            product,
            quantity,
            seller_profile,
            enforce_stock=False,
        )
        purchase_quote = _quote_commercial(
            product,
            quantity,
            seller_profile,
            enforce_stock=True,
        )
        availability = classify_kit_line(product, purchase_quote)
        show_price = availability != LINE_UNCONFIRMED
        can_buy = availability == LINE_AVAILABLE
        quote = purchase_quote if enforce_stock else display_quote
        lines.append(KitLineView(
            item=item,
            product=product,
            quantity=quantity,
            unit_price=display_quote.unit_price if show_price else None,
            subtotal=display_quote.total_price if show_price else None,
            can_buy=can_buy,
            reason=purchase_quote.reason or '',
            quote=quote,
            display_name=kit_component_display_name(product),
            availability=availability,
            availability_label=_availability_label(availability, product),
            selected_by_default=can_buy,
            request_url=_line_request_url(availability),
        ))
    return lines


def _sum_line_totals(lines, predicate) -> int | None:
    selected = [line for line in lines if predicate(line)]
    if not selected:
        return None
    totals = [line.subtotal for line in selected]
    if any(value is None for value in totals):
        return None
    return sum(totals)


def build_kit_view(kit: MaintenanceKit, request=None) -> KitView:
    seller_profile = get_request_seller_profile(request) if request is not None else None
    display_lines = quote_kit_lines(kit, seller_profile, enforce_stock=True)
    selected_total = _sum_line_totals(
        display_lines,
        lambda line: line.selected_by_default,
    )
    full_total = _sum_line_totals(
        display_lines,
        lambda line: line.availability != LINE_UNCONFIRMED,
    )
    available = available_kits(kit)
    has_unavailable = any(line.availability == LINE_UNAVAILABLE for line in display_lines)
    has_unconfirmed = any(line.availability == LINE_UNCONFIRMED for line in display_lines)
    orderable_count = sum(1 for line in display_lines if line.can_buy)
    reason = ''
    can_add = True
    try:
        if request is not None:
            preflight_add_kit(request, kit)
        else:
            default_items = [line.item for line in display_lines if line.selected_by_default]
            _preflight_kit_contents(kit, default_items)
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
        reference_lines=kit_reference_line_views(kit),
        total_price=selected_total,
        full_total_price=full_total,
        available_kits=available,
        can_add=can_add,
        reason=reason,
        has_unavailable=has_unavailable,
        has_unconfirmed=has_unconfirmed,
        orderable_count=orderable_count,
    )


def guest_kit_base_price(kit: MaintenanceKit) -> int | None:
    """Admin-facing guest commercial total of the full confirmed composition."""
    lines = quote_kit_lines(kit, seller_profile=None, enforce_stock=False)
    return _sum_line_totals(
        lines,
        lambda line: line.availability != LINE_UNCONFIRMED,
    )


def parse_selected_product_ids(raw_values) -> list[int]:
    """Parse posted item ids. Ignores prices; rejects non-integer values."""
    ids = []
    seen = set()
    for value in raw_values or []:
        text = str(value or '').strip().replace('\xa0', '').replace(' ', '')
        if not text:
            continue
        if not text.isdigit():
            raise MaintenanceKitCartError(KIT_INVALID_SELECTION_MESSAGE)
        pk = int(text)
        if pk in seen:
            continue
        seen.add(pk)
        ids.append(pk)
    return ids


def _default_orderable_items(kit: MaintenanceKit, seller_profile=None):
    items = []
    for item in kit.items.all():
        quantity = int(item.quantity)
        purchase_quote = _quote_commercial(
            item.product,
            quantity,
            seller_profile,
            enforce_stock=True,
        )
        if classify_kit_line(item.product, purchase_quote) == LINE_AVAILABLE:
            items.append(item)
    return items


def _preflight_kit_contents(kit: MaintenanceKit, items=None):
    if not kit.is_active:
        raise MaintenanceKitCartError(KIT_INACTIVE_MESSAGE)
    all_items = list(kit.items.all())
    if not all_items:
        raise MaintenanceKitCartError(KIT_EMPTY_MESSAGE)
    if items is None:
        items = all_items
    items = list(items)
    if not items:
        raise MaintenanceKitCartError(KIT_EMPTY_SELECTION_MESSAGE)

    allowed_ids = {item.product_id for item in all_items}
    synthetic = []
    for item in items:
        quantity = int(item.quantity)
        if quantity < 1:
            raise MaintenanceKitCartError('Количество в комплекте должно быть не меньше 1.')
        product = item.product
        if item.product_id not in allowed_ids:
            raise MaintenanceKitCartError(KIT_INVALID_SELECTION_MESSAGE)
        if product is None or product.pk is None:
            raise MaintenanceKitCartError(KIT_LINE_UNAVAILABLE_MESSAGE)
        article = str(getattr(product, 'article', '') or '').strip()
        if not article:
            raise MaintenanceKitCartError(KIT_LINE_UNAVAILABLE_MESSAGE)
        if getattr(product, 'status', '') != 'active':
            raise MaintenanceKitCartError(KIT_LINE_UNAVAILABLE_MESSAGE)
        try:
            validate_product_for_cart(synthetic, product)
        except CartSellerConflictError:
            raise MaintenanceKitCartError(KIT_INTERNAL_SELLER_MESSAGE)
        synthetic.append({'product': product, 'quantity': quantity})
    return items


def resolve_selected_kit_items(kit: MaintenanceKit, selected_ids: list[int]):
    items_by_product = {item.product_id: item for item in kit.items.all()}
    if not selected_ids:
        raise MaintenanceKitCartError(KIT_EMPTY_SELECTION_MESSAGE)
    selected = []
    for product_id in selected_ids:
        item = items_by_product.get(product_id)
        if item is None:
            raise MaintenanceKitCartError(KIT_INVALID_SELECTION_MESSAGE)
        selected.append(item)
    return selected


def preflight_add_kit(request, kit: MaintenanceKit, selected_ids=None):
    seller_profile = get_request_seller_profile(request)
    if selected_ids is None:
        items = _default_orderable_items(kit, seller_profile)
    else:
        items = resolve_selected_kit_items(kit, selected_ids)
    items = _preflight_kit_contents(kit, items)
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
                quote.reason or KIT_LINE_UNAVAILABLE_MESSAGE
            )
    return items, cart


def add_kit_to_cart(request, kit: MaintenanceKit, selected_ids=None):
    """Add selected kit components or leave the cart unchanged.

    Quantities always come from MaintenanceKitItem. Client totals are ignored.
    """
    items, cart = preflight_add_kit(request, kit, selected_ids=selected_ids)

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
    """Only kits explicitly published as a verified composition.

    Does not infer cars from Brand/CarModel or Product names.
    Zero stock of a part does not exclude the kit.
    """
    return (
        MaintenanceKit.objects.filter(is_active=True)
        .select_related('brand', 'car_model', 'car_model__brand')
        .prefetch_related(
            'items__product',
        )
    )
