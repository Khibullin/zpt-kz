from catalog.models import Product

from .constants import SESSION_CART_KEY


class CartManager:
    """Session-based shopping cart for ZPT Market checkout."""

    def __init__(self, request):
        self.request = request
        if SESSION_CART_KEY not in self.request.session:
            self.request.session[SESSION_CART_KEY] = {}

    def _cart(self):
        return self.request.session.setdefault(SESSION_CART_KEY, {})

    def add(self, product_id, quantity=1):
        quantity = max(1, int(quantity))
        cart = self._cart()
        key = str(product_id)
        cart[key] = cart.get(key, 0) + quantity
        self.request.session.modified = True

    def set_quantity(self, product_id, quantity):
        cart = self._cart()
        key = str(product_id)
        quantity = int(quantity)
        if quantity <= 0:
            cart.pop(key, None)
        else:
            cart[key] = quantity
        self.request.session.modified = True

    def remove(self, product_id):
        self._cart().pop(str(product_id), None)
        self.request.session.modified = True

    def clear(self):
        self.request.session[SESSION_CART_KEY] = {}
        self.request.session.modified = True

    def is_empty(self):
        return not self._cart()

    def get_count(self):
        return sum(self._cart().values())

    def get_product_quantities(self):
        return {int(product_id): qty for product_id, qty in self._cart().items()}

    def get_items(self):
        quantities = self.get_product_quantities()
        if not quantities:
            return []

        products = Product.objects.filter(
            id__in=quantities.keys(),
            status='active',
        ).select_related('brand', 'car_model')

        product_map = {product.id: product for product in products}
        items = []

        for product_id, quantity in quantities.items():
            product = product_map.get(product_id)
            if not product:
                continue
            items.append({
                'product': product,
                'quantity': quantity,
                'line_total': product.price * quantity,
            })

        return items

    def get_total(self):
        return sum(item['line_total'] for item in self.get_items())

    def prune_invalid(self):
        """Remove inactive or missing products from the cart."""
        valid_ids = {
            product.id
            for product in Product.objects.filter(
                id__in=self.get_product_quantities().keys(),
                status='active',
            )
        }
        cart = self._cart()
        for product_id in list(cart.keys()):
            if int(product_id) not in valid_ids:
                cart.pop(product_id, None)
        self.request.session.modified = True
