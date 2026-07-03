import re


class CartSellerConflictError(Exception):
    def __init__(self, seller_name):
        self.seller_name = seller_name or 'продавца'
        super().__init__(self.seller_name)


def normalize_seller_whatsapp(phone):
    digits = re.sub(r'\D', '', str(phone or ''))
    if digits.startswith('8') and len(digits) == 11:
        digits = '7' + digits[1:]
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
