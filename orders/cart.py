from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.db.models import Sum

from catalog.commercial import (
    b2b_prefetch,
    get_request_seller_profile,
    resolve_commercial_price,
)
from catalog.models import Brand, Country, Product
from catalog.wholesale import (
    is_wholesale_eligible,
    quote_public_wholesale,
    remaining_wholesale_qty,
    resolve_wholesale_owner,
)

from .constants import (
    CART_MODE_RETAIL,
    CART_MODE_WHOLESALE,
    SESSION_CART_KEY,
    SESSION_CART_MODE_KEY,
)
from .models import CartItem
from .seller_utils import (
    CartModeConflictError,
    CartSellerConflictError,
    validate_product_for_cart,
)


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

        mode = self.request.session.get(SESSION_CART_MODE_KEY)
        try:
            for product_id, quantity in session_cart.items():
                self.add(
                    int(product_id),
                    int(quantity),
                    accumulate=True,
                    mode=mode,
                )
        except (CartModeConflictError, CartSellerConflictError, ValueError):
            return

        self.request.session.pop(SESSION_CART_KEY, None)
        self.request.session.modified = True

    def _normalize_product_id(self, product_id):
        return int(product_id)

    def get_mode(self):
        mode = self.request.session.get(SESSION_CART_MODE_KEY)
        if mode in (CART_MODE_RETAIL, CART_MODE_WHOLESALE):
            return mode
        if self.get_count():
            return CART_MODE_RETAIL
        return None

    def is_wholesale(self):
        return self.get_mode() == CART_MODE_WHOLESALE

    def _set_mode(self, mode):
        if mode in (CART_MODE_RETAIL, CART_MODE_WHOLESALE):
            self.request.session[SESSION_CART_MODE_KEY] = mode
        else:
            self.request.session.pop(SESSION_CART_MODE_KEY, None)
        self.request.session.modified = True

    def _normalize_mode(self, mode):
        if mode == CART_MODE_WHOLESALE:
            return CART_MODE_WHOLESALE
        return CART_MODE_RETAIL

    def _mode_conflict_message(self, requested_mode):
        if requested_mode == CART_MODE_WHOLESALE:
            return (
                'В корзине уже есть розничные товары. '
                'Оформите текущий заказ или очистите корзину, чтобы купить оптом.'
            )
        return (
            'В корзине уже есть оптовые товары. '
            'Оформите оптовый заказ или очистите корзину, чтобы купить в розницу.'
        )

    def _ensure_mode(self, requested_mode):
        current = self.get_mode()
        if not self.get_count() or current is None:
            self._set_mode(requested_mode)
            return
        if current != requested_mode:
            raise CartModeConflictError(self._mode_conflict_message(requested_mode))

    def wholesale_status(self):
        if not self.is_wholesale():
            return {
                'enabled': False,
                'seller': None,
                'total_qty': 0,
                'minimum': 0,
                'remaining': 0,
                'can_checkout': True,
            }
        items = self.get_items()
        seller = resolve_wholesale_owner(items[0]['product']) if items else None
        total_qty = sum(item['quantity'] for item in items)
        minimum = int(getattr(seller, 'wholesale_min_order_qty', 0) or 0) if seller else 0
        remaining = remaining_wholesale_qty(total_qty, seller) if seller else 0
        return {
            'enabled': True,
            'seller': seller,
            'total_qty': total_qty,
            'minimum': minimum,
            'remaining': remaining,
            'can_checkout': bool(seller and remaining == 0 and total_qty > 0),
        }

    def _seller_profile(self):
        return get_request_seller_profile(self.request)

    def _quote(self, product, quantity):
        if self.is_wholesale():
            return quote_public_wholesale(product, quantity)
        return resolve_commercial_price(
            product,
            quantity,
            seller_profile=self._seller_profile(),
        )

    def _require_purchasable(self, product, quantity):
        quote = self._quote(product, quantity)
        if not quote.can_buy:
            raise ValueError(quote.reason)
        return quote

    def add(self, product_id, quantity=1, accumulate=True, mode=None):
        product_id = self._normalize_product_id(product_id)
        try:
            requested_qty = int(quantity)
        except (TypeError, ValueError):
            raise ValueError('Укажите количество больше 0.')

        requested_mode = self._normalize_mode(mode)

        product = Product.objects.filter(pk=product_id, status='active').first()
        if not product:
            raise ValueError('Product not found')

        if requested_mode == CART_MODE_WHOLESALE:
            owner = resolve_wholesale_owner(product)
            if owner is None or not is_wholesale_eligible(product, owner):
                raise ValueError('Этот товар нельзя купить оптом.')

        self._ensure_mode(requested_mode)

        if requested_mode == CART_MODE_WHOLESALE:
            existing_items = self.get_items()
            if existing_items:
                current_owner = resolve_wholesale_owner(existing_items[0]['product'])
                if current_owner is None or owner.pk != current_owner.pk:
                    raise CartSellerConflictError(
                        getattr(current_owner, 'name', None) or 'продавца'
                    )
        else:
            validate_product_for_cart(self.get_items(), product)

        current_qty = self.get_product_quantities().get(product_id, 0)
        resulting_qty = current_qty + requested_qty if accumulate else requested_qty
        self._require_purchasable(product, resulting_qty)

        if self.user:
            item, created = CartItem.objects.get_or_create(
                user=self.user,
                product_id=product_id,
                defaults={'quantity': requested_qty},
            )
            if not created:
                item.quantity = (
                    item.quantity + requested_qty if accumulate else requested_qty
                )
                item.save(update_fields=['quantity', 'updated_at'])
            return

        cart = self._session_cart()
        key = str(product_id)
        if accumulate:
            cart[key] = cart.get(key, 0) + requested_qty
        else:
            cart[key] = requested_qty
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

        self._require_purchasable(product, quantity)

        if product_id not in self.get_product_quantities():
            if self.is_wholesale():
                owner = resolve_wholesale_owner(product)
                if owner is None or not is_wholesale_eligible(product, owner):
                    raise ValueError('Этот товар нельзя купить оптом.')
            else:
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
        else:
            self._session_cart().pop(str(product_id), None)
            self.request.session.modified = True
        if self.get_count() == 0:
            self._set_mode(None)

    def clear(self):
        if self.user:
            CartItem.objects.filter(user=self.user).delete()
        self.request.session.pop(SESSION_CART_KEY, None)
        self._set_mode(None)
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
        ).select_related('brand', 'car_model', 'seller_profile')

        seller_profile = self._seller_profile()
        if seller_profile is not None and not self.is_wholesale():
            products = b2b_prefetch(products)

        product_map = {product.id: product for product in products}
        items = []

        for product_id, quantity in quantities.items():
            product = product_map.get(product_id)
            if not product:
                continue
            quote = self._quote(product, quantity)
            unit_price = quote.unit_price
            line_total = quote.total_price if quote.can_buy else 0
            items.append({
                'product': product,
                'quantity': quantity,
                'unit_price': unit_price,
                'line_total': line_total if line_total is not None else 0,
                'price_type': quote.price_type,
                'price_label': quote.label,
                'can_buy': quote.can_buy,
                'quote_reason': quote.reason,
                'quote': quote,
            })

        return items

    def get_total(self):
        return sum(item['line_total'] for item in self.get_items())

    def get_or_create_virtual_product(self, product_data):
        return CartManager.get_or_create_virtual_product(product_data)

    def prune_invalid(self):
        quantities = self.get_product_quantities()
        if not quantities:
            return 0

        valid_ids = {
            product.id
            for product in Product.objects.filter(
                id__in=quantities.keys(),
                status='active',
            )
        }
        if self.is_wholesale():
            eligible_ids = set()
            for product in Product.objects.filter(id__in=valid_ids).select_related('seller_profile'):
                owner = resolve_wholesale_owner(product)
                if owner is not None and is_wholesale_eligible(product, owner):
                    eligible_ids.add(product.id)
            valid_ids = eligible_ids
        removed = 0

        if self.user:
            stale = CartItem.objects.filter(user=self.user).exclude(
                product_id__in=valid_ids
            )
            removed = stale.count()
            stale.delete()
        else:
            cart = self._session_cart()
            for product_id in list(cart.keys()):
                if int(product_id) not in valid_ids:
                    cart.pop(product_id, None)
                    removed += 1
            self.request.session.modified = True

        if self.get_count() == 0:
            self._set_mode(None)

        return removed

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
