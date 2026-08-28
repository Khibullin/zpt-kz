import re

from django.conf import settings

from catalog.models import Product, SellerProfile

from .constants import DEFAULT_WAREHOUSE_ADDRESS


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


def get_product_seller_key(product):
    return normalize_seller_whatsapp(product.whatsapp_number)


def get_seller_snapshot_from_items(items):
    if not items:
        raise ValueError('Cart is empty')

    first_product = items[0]['product']
    seller_key = get_product_seller_key(first_product)
    seller_name = (first_product.seller_name or '').strip()
    seller_whatsapp = (first_product.whatsapp_number or '').strip()

    for item in items[1:]:
        if get_product_seller_key(item['product']) != seller_key:
            raise CartSellerConflictError(seller_name)

    return {
        'seller_name': seller_name,
        'seller_whatsapp': seller_whatsapp,
    }


def validate_product_for_cart(cart_items, product):
    if not cart_items:
        return

    current_name = (cart_items[0]['product'].seller_name or '').strip() or 'продавца'
    current_key = get_product_seller_key(cart_items[0]['product'])
    new_key = get_product_seller_key(product)

    if new_key != current_key:
        raise CartSellerConflictError(current_name)


def resolve_seller_profile_from_items(items):
    """
    Soft-resolve SellerProfile for cart items via normalized WhatsApp
    (same last-10 digit legacy matching as catalog attach_sellers).

    If multiple profiles share the same phone suffix, the lowest pk wins.
    """
    if not items:
        return None

    product = items[0]['product']
    suffix = seller_phone_suffix(product.whatsapp_number)
    if not suffix:
        return None

    for profile in SellerProfile.objects.order_by('pk').iterator():
        if seller_phone_suffix(profile.phone) == suffix:
            return profile
    return None


def resolve_seller_profile_from_order(order):
    """Resolve SellerProfile from order seller snapshot (WhatsApp, then name)."""
    if order is None:
        return None
    suffix = seller_phone_suffix(order.seller_whatsapp)
    if suffix:
        for profile in SellerProfile.objects.order_by('pk').iterator():
            if seller_phone_suffix(profile.phone) == suffix:
                return profile
    name = (order.seller_name or '').strip()
    if name:
        return SellerProfile.objects.filter(name__iexact=name).order_by('pk').first()
    return None


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

    seller_profile = resolve_seller_profile_from_items(items)
    if seller_profile is not None:
        address = seller_profile.get_effective_pickup_address()
        available = bool(seller_profile.pickup_available and address)
        return {
            'seller_profile': seller_profile,
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
