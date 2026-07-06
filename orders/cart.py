from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.db.models import Sum

from catalog.models import Brand, Country, Product

from .constants import SESSION_CART_KEY
from .models import CartItem
from .seller_utils import CartSellerConflictError, validate_product_for_cart


class CartManager:
    """
    Shopping cart backed by the database for authenticated users
    and Django sessions for guests.
    """

    def __init__(self, request):
        self.request = request
        self.user = request.user if getattr(request.user, 'is_authenticated', False) else None
        if self.user:
            self._merge_session_into_db()
        else:
            self._ensure_session_cart()

    def _ensure_session_cart(self):
        if SESSION_CART_KEY not in self.request.session:
            self.request.session[SESSION_CART_KEY] = {}

    def _session_cart(self):
        self._ensure_session_cart()
        return self.request.session[SESSION_CART_KEY]

    def _merge_session_into_db(self):
        session_cart = self.request.session.get(SESSION_CART_KEY, {})
        if not session_cart:
            return

        for product_id, quantity in session_cart.items():
            self.add(int(product_id), int(quantity), accumulate=True)

        self.request.session.pop(SESSION_CART_KEY, None)
        self.request.session.modified = True

    def _normalize_product_id(self, product_id):
        return int(product_id)

    def add(self, product_id, quantity=1, accumulate=True):
        product_id = self._normalize_product_id(product_id)
        quantity = max(1, int(quantity))

        product = Product.objects.filter(pk=product_id, status='active').first()
        if not product:
            raise ValueError('Product not found')

        if product.price_on_request:
            raise ValueError('Этот товар доступен только по запросу цены через WhatsApp')

        validate_product_for_cart(self.get_items(), product)

        if self.user:
            item, created = CartItem.objects.get_or_create(
                user=self.user,
                product_id=product_id,
                defaults={'quantity': quantity},
            )
            if not created:
                item.quantity = item.quantity + quantity if accumulate else quantity
                item.save(update_fields=['quantity', 'updated_at'])
            return

        cart = self._session_cart()
        key = str(product_id)
        if accumulate:
            cart[key] = cart.get(key, 0) + quantity
        else:
            cart[key] = quantity
        self.request.session.modified = True

    def set_quantity(self, product_id, quantity):
        product_id = self._normalize_product_id(product_id)
        quantity = int(quantity)
        if quantity <= 0:
            self.remove(product_id)
            return

        product = Product.objects.filter(pk=product_id, status='active').first()
        if not product:
            raise ValueError('Product not found')

        if product.price_on_request:
            raise ValueError('Этот товар доступен только по запросу цены через WhatsApp')

        if product_id not in self.get_product_quantities():
            validate_product_for_cart(self.get_items(), product)

        if self.user:
            CartItem.objects.update_or_create(
                user=self.user,
                product_id=product_id,
                defaults={'quantity': quantity},
            )
            return

        self._session_cart()[str(product_id)] = quantity
        self.request.session.modified = True

    def remove(self, product_id):
        product_id = self._normalize_product_id(product_id)
        if self.user:
            CartItem.objects.filter(user=self.user, product_id=product_id).delete()
            return

        self._session_cart().pop(str(product_id), None)
        self.request.session.modified = True

    def clear(self):
        if self.user:
            CartItem.objects.filter(user=self.user).delete()
        self.request.session.pop(SESSION_CART_KEY, None)
        self.request.session.modified = True

    def is_empty(self):
        return self.get_count() == 0

    def get_count(self):
        if self.user:
            return CartItem.objects.filter(user=self.user).aggregate(
                total=Sum('quantity')
            )['total'] or 0
        return sum(self._session_cart().values())

    def get_total_items(self):
        return self.get_count()

    def get_product_quantities(self):
        if self.user:
            return {
                item.product_id: item.quantity
                for item in CartItem.objects.filter(user=self.user)
            }
        return {int(product_id): qty for product_id, qty in self._session_cart().items()}

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

    def get_or_create_virtual_product(self, product_data):
        return CartManager.get_or_create_virtual_product(product_data)

    def prune_invalid(self):
        valid_ids = {
            product.id
            for product in Product.objects.filter(
                id__in=self.get_product_quantities().keys(),
                status='active',
            )
        }

        if self.user:
            CartItem.objects.filter(user=self.user).exclude(
                product_id__in=valid_ids
            ).delete()
        else:
            cart = self._session_cart()
            for product_id in list(cart.keys()):
                if int(product_id) not in valid_ids:
                    cart.pop(product_id, None)
            self.request.session.modified = True

    @staticmethod
    def get_or_create_virtual_product(product_data):
        """
        Create or refresh a local Product from external supplier data (Phaeton).

        Expected keys: sku, brand, price, name, supplier (default: phaeton).
        """
        sku = str(product_data.get('sku') or product_data.get('article') or '').strip()
        if not sku:
            raise ValueError('sku is required for virtual products')

        supplier = product_data.get('supplier', Product.SUPPLIER_PHAETON)
        brand_name = str(product_data.get('brand') or '').strip()
        title = str(product_data.get('name') or sku).strip()

        markup_percent = Decimal(str(getattr(settings, 'PHAETON_PRICE_MARKUP_PERCENT', 15)))
        base_price = Decimal(str(product_data.get('price', 0)))
        final_price = int(
            (base_price * (Decimal('1') + markup_percent / Decimal('100'))).quantize(
                Decimal('1'),
                rounding=ROUND_HALF_UP,
            )
        )
        final_price = max(final_price, 0)

        brand_obj = None
        if brand_name:
            country, _ = Country.objects.get_or_create(name='Прочее')
            brand_obj, _ = Brand.objects.get_or_create(
                country=country,
                name=brand_name,
            )

        lookup = Product.objects.filter(article=sku, supplier=supplier)
        if brand_obj:
            lookup = lookup.filter(brand=brand_obj)
        else:
            lookup = lookup.filter(brand__isnull=True)

        defaults = {
            'title': title,
            'price': final_price,
            'status': 'active',
            'condition': 'new',
            'seller_name': 'Phaeton (ZPT)',
            'whatsapp_number': getattr(settings, 'ZPT_DEFAULT_WHATSAPP', '+77713607040'),
            'city': getattr(settings, 'ZPT_WAREHOUSE_CITY', 'Алматы'),
            'description': product_data.get('description', ''),
            'compatibility': product_data.get('compatibility', ''),
        }

        with transaction.atomic():
            product = lookup.select_for_update().first()
            if product:
                for field, value in defaults.items():
                    setattr(product, field, value)
                product.save()
            else:
                product = Product.objects.create(
                    article=sku,
                    supplier=supplier,
                    brand=brand_obj,
                    **defaults,
                )

        return product
