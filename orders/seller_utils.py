import re
from dataclasses import dataclass

from django.conf import settings

from catalog.models import Product, SellerProfile

from .constants import DEFAULT_WAREHOUSE_ADDRESS

# TODO: a stored normalized phone would replace the Python scan of SellerProfile.phone.


class CartSellerConflictError(Exception):
    def __init__(self, seller_name):
        self.seller_name = seller_name or 'продавца'
        super().__init__(self.seller_name)


class CartModeConflictError(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)


def normalize_seller_whatsapp(phone):
    digits = re.sub(r'\D', '', str(phone or ''))
    if digits.startswith('8') and len(digits) == 11:
        digits = '7' + digits[1:]
    return digits


def seller_phone_suffix(phone):
    digits = normalize_seller_whatsapp(phone)
    if len(digits) >= 10:
        return digits[-10:]
    return digits


RESOLUTION_RESOLVED = 'resolved'
RESOLUTION_NOT_FOUND = 'not_found'
RESOLUTION_AMBIGUOUS = 'ambiguous'
RESOLUTION_CONFLICT = 'conflict'

SOURCE_EXPLICIT = 'explicit'
SOURCE_WHATSAPP = 'whatsapp'
SOURCE_NAME = 'name'


@dataclass(frozen=True)
class SellerResolution:
    profile: SellerProfile | None
    status: str
    source: str | None = None


def _not_found():
    return SellerResolution(None, RESOLUTION_NOT_FOUND, None)


def get_product_seller_key(product):
    """Cart identity. Explicit SellerProfile wins over a shared WhatsApp number."""
    profile_id = getattr(product, 'seller_profile_id', None)
    if profile_id:
        return f'profile:{profile_id}'
    return f'whatsapp:{normalize_seller_whatsapp(product.whatsapp_number)}'


def cart_has_single_seller(products):
    """Whether these products may stay in one retail cart.

    Explicit SellerProfile links are compared by FK: different merchants conflict
    even with one WhatsApp, and one merchant does not split on phone formatting.
    Products without FK keep the normalized WhatsApp rule. A legacy line can sit
    next to an explicit product only when that snapshot number matches.
    """
    explicit_ids = {
        product.seller_profile_id
        for product in products
        if getattr(product, 'seller_profile_id', None)
    }
    if len(explicit_ids) > 1:
        return False
    legacy_phones = {
        normalize_seller_whatsapp(getattr(product, 'whatsapp_number', ''))
        for product in products
        if not getattr(product, 'seller_profile_id', None)
    }
    if len(legacy_phones) > 1:
        return False
    if not explicit_ids or not legacy_phones:
        return True
    explicit_phones = {
        normalize_seller_whatsapp(getattr(product, 'whatsapp_number', ''))
        for product in products
        if getattr(product, 'seller_profile_id', None)
    }
    return explicit_phones == legacy_phones


def _require_single_cart_seller(products):
    if not products:
        raise ValueError('Cart is empty')
    if cart_has_single_seller(products):
        return
    seller_name = (getattr(products[0], 'seller_name', '') or '').strip() or 'продавца'
    raise CartSellerConflictError(seller_name)


def get_seller_snapshot_from_items(items):
    if not items:
        raise ValueError('Cart is empty')

    products = [item['product'] for item in items]
    _require_single_cart_seller(products)
    first_product = products[0]
    seller_name = (first_product.seller_name or '').strip()
    seller_whatsapp = (first_product.whatsapp_number or '').strip()

    return {
        'seller_name': seller_name,
        'seller_whatsapp': seller_whatsapp,
    }


def validate_product_for_cart(cart_items, product):
    if not cart_items:
        return

    products = [item['product'] for item in cart_items]
    products.append(product)
    _require_single_cart_seller(products)


def _profiles_by_phone_suffix():
    """One pass over merchant phones. Normalization stays in Python."""
    buckets = {}
    for profile in SellerProfile.objects.all().iterator():
        suffix = seller_phone_suffix(profile.phone)
        if not suffix:
            continue
        buckets.setdefault(suffix, []).append(profile)
    return buckets


def resolve_unique_seller_profile_by_whatsapp(phone, *, profiles_by_suffix=None):
    suffix = seller_phone_suffix(phone)
    if not suffix:
        return _not_found()
    if profiles_by_suffix is None:
        profiles_by_suffix = _profiles_by_phone_suffix()
    matches = profiles_by_suffix.get(suffix, [])
    if len(matches) == 1:
        return SellerResolution(matches[0], RESOLUTION_RESOLVED, SOURCE_WHATSAPP)
    if not matches:
        return _not_found()
    return SellerResolution(None, RESOLUTION_AMBIGUOUS, SOURCE_WHATSAPP)


def resolve_unique_seller_profile_by_name(name):
    """Exact case-insensitive name. Several rows are ambiguous, never the first pk."""
    cleaned = (name or '').strip()
    if not cleaned:
        return _not_found()
    matches = list(SellerProfile.objects.filter(name__iexact=cleaned)[:2])
    if len(matches) == 1:
        return SellerResolution(matches[0], RESOLUTION_RESOLVED, SOURCE_NAME)
    if not matches:
        return _not_found()
    return SellerResolution(None, RESOLUTION_AMBIGUOUS, SOURCE_NAME)


def _is_phaeton_product(product):
    return getattr(product, 'supplier', None) == Product.SUPPLIER_PHAETON


def resolve_product_merchant(product, *, profiles_by_suffix=None):
    """Explicit Product.seller_profile, otherwise a unique WhatsApp match.

    Phaeton products do not inherit a merchant from a shared warehouse phone.
    Name matching is not used for products.
    """
    if product is None:
        return _not_found()
    if getattr(product, 'seller_profile_id', None):
        return SellerResolution(product.seller_profile, RESOLUTION_RESOLVED, SOURCE_EXPLICIT)
    if _is_phaeton_product(product):
        return _not_found()
    return resolve_unique_seller_profile_by_whatsapp(
        getattr(product, 'whatsapp_number', ''),
        profiles_by_suffix=profiles_by_suffix,
    )


def _item_product(item):
    if isinstance(item, dict):
        return item.get('product')
    return item


def resolve_canonical_merchant_from_items(items):
    """One merchant for a cart, or no guess.

    Every line must resolve to the same SellerProfile. A legacy line that is
    missing, ambiguous, or a different merchant leaves the order unlinked.
    """
    products = [_item_product(item) for item in (items or [])]
    products = [product for product in products if product is not None]
    if not products:
        return _not_found()

    needs_phone = any(
        not getattr(product, 'seller_profile_id', None) and not _is_phaeton_product(product)
        for product in products
    )
    profiles_by_suffix = _profiles_by_phone_suffix() if needs_phone else {}
    resolutions = [
        resolve_product_merchant(product, profiles_by_suffix=profiles_by_suffix)
        for product in products
    ]
    if any(item.status == RESOLUTION_AMBIGUOUS for item in resolutions):
        source = next(item.source for item in resolutions if item.status == RESOLUTION_AMBIGUOUS)
        return SellerResolution(None, RESOLUTION_AMBIGUOUS, source)

    resolved = [item for item in resolutions if item.status == RESOLUTION_RESOLVED]
    if len(resolved) != len(products):
        return _not_found()
    profile_ids = {item.profile.pk for item in resolved}
    if len(profile_ids) != 1:
        return SellerResolution(None, RESOLUTION_CONFLICT, SOURCE_EXPLICIT)
    sources = {item.source for item in resolved}
    source = SOURCE_EXPLICIT if SOURCE_EXPLICIT in sources else next(iter(sources))
    return SellerResolution(resolved[0].profile, RESOLUTION_RESOLVED, source)


def resolve_seller_profile_from_items(items):
    """Merchant for cart items, or None when the match is missing or not unique."""
    return resolve_canonical_merchant_from_items(items).profile


def resolve_order_merchant(order):
    """Stored FK first, then unique WhatsApp, then a unique exact name."""
    if order is None:
        return _not_found()
    if getattr(order, 'seller_profile_id', None):
        return SellerResolution(order.seller_profile, RESOLUTION_RESOLVED, SOURCE_EXPLICIT)
    phone_match = resolve_unique_seller_profile_by_whatsapp(getattr(order, 'seller_whatsapp', ''))
    if phone_match.status != RESOLUTION_NOT_FOUND:
        return phone_match
    return resolve_unique_seller_profile_by_name(getattr(order, 'seller_name', ''))


def resolve_seller_profile_from_order(order):
    """Legacy order display. Ambiguous phone or name stays unresolved."""
    return resolve_order_merchant(order).profile


def warehouse_address_fallback():
    return getattr(settings, 'ZPT_WAREHOUSE_ADDRESS', DEFAULT_WAREHOUSE_ADDRESS)


def resolve_pickup_options(items):
    """
    Determine whether pickup is offered at checkout and which address to show.

    Precedence:
      1. Phaeton/API products always use ZPT_WAREHOUSE_ADDRESS
         (never a SellerProfile matched by shared WhatsApp).
      2. Local products with a matched SellerProfile use that profile.
      3. Local products without a profile: pickup unavailable.

    Returns:
        dict with keys:
          - seller_profile: SellerProfile | None
          - pickup_available: bool
          - effective_pickup_address: str
    """
    product = items[0]['product'] if items else None
    if product is not None and getattr(product, 'supplier', None) == Product.SUPPLIER_PHAETON:
        address = warehouse_address_fallback()
        return {
            'seller_profile': None,
            'pickup_available': bool(address),
            'effective_pickup_address': address,
        }

    merchant_profile = resolve_canonical_merchant_from_items(items).profile
    if merchant_profile is not None:
        address = merchant_profile.get_effective_pickup_address()
        available = bool(merchant_profile.pickup_available and address)
        return {
            'seller_profile': merchant_profile,
            'pickup_available': available,
            'effective_pickup_address': address if available else '',
        }

    return {
        'seller_profile': None,
        'pickup_available': False,
        'effective_pickup_address': '',
    }


def get_order_pickup_display_address(order):
    """
    Pickup address for display/email.

    Prefer snapshot in delivery_address['address']; legacy pickup
    ({'type': 'pickup'} without address) falls back to ZPT warehouse.
    """
    payload = order.delivery_address or {}
    snapshot = payload.get('address')
    if isinstance(snapshot, str) and snapshot.strip():
        return snapshot.strip()
    return warehouse_address_fallback()
