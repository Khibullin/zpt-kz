import json

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import (
    Product,
    ProductPriceTier,
    SellerProfile,
)
from orders.cart import CartManager
from orders.models import Order, OrderItem
from orders.tests.test_manual_checkout import ensure_seller_profile_for_product


RETAIL = 2000


def _buyer_seller(username='buyer-seller'):
    user = User.objects.create_user(username=username, password='secret12345')
    return SellerProfile.objects.create(
        user=user,
        name='Buyer Seller',
        phone='77770000921',
        city='Алматы',
    )


def _product(**kwargs):
    defaults = {
        'title': 'B2B cart product',
        'price': RETAIL,
        'cost_price': 87654321,
        'seller_name': 'AG Parts',
        'whatsapp_number': '+77771234567',
        'status': 'active',
        'slug': 'b2b-cart-product',
        'article': 'B2B-CART',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    ORDER_ADMIN_EMAIL='orders-admin@test.local',
)
class B2BCartCheckoutTests(TestCase):
    def setUp(self):
        self.buyer = _buyer_seller()
        self.product = _product()
        ProductPriceTier.objects.create(product=self.product, min_qty=10, price=1200)
        ProductPriceTier.objects.create(product=self.product, min_qty=20, price=1050)
        ProductPriceTier.objects.create(product=self.product, min_qty=30, price=900)
        ensure_seller_profile_for_product(self.product, overwrite=False)

    def _add(self, product=None, quantity=1, extra=None):
        payload = {
            'product_id': (product or self.product).id,
            'quantity': quantity,
        }
        if extra:
            payload.update(extra)
        return self.client.post(
            reverse('orders:cart_add_api'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def _update(self, quantity, extra=None):
        payload = {
            'product_id': self.product.id,
            'quantity': quantity,
        }
        if extra:
            payload.update(extra)
        return self.client.post(
            reverse('orders:cart_update_quantity'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def _checkout(self):
        self.client.get(reverse('orders:checkout'))
        return self.client.post(
            reverse('orders:checkout'),
            data={
                'customer_name': 'Иван',
                'customer_phone': '+7 (701) 123-45-67',
                'delivery_method': Order.DELIVERY_PICKUP,
            },
        )

    def test_guest_cart_stays_retail(self):
        response = self._add(quantity=20)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['cart_total'], RETAIL * 20)
        cart = self.client.get(reverse('orders:cart'))
        self.assertEqual(cart.context['cart_total'], RETAIL * 20)
        self.assertEqual(cart.context['items'][0]['unit_price'], RETAIL)

    def test_plain_user_cart_stays_retail(self):
        User.objects.create_user(username='plain-cart', password='secret12345')
        self.client.login(username='plain-cart', password='secret12345')
        response = self._add(quantity=20)
        self.assertEqual(response.json()['cart_total'], RETAIL * 20)

    def test_seller_cart_uses_wholesale(self):
        self.client.login(username='buyer-seller', password='secret12345')
        response = self._add(quantity=20)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['cart_total'], 1050 * 20)

    def test_quantity_change_recalculates_tier(self):
        self.client.login(username='buyer-seller', password='secret12345')
        self._add(quantity=15)
        updated = self._update(25)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()['unit_price'], 1050)
        self.assertEqual(updated.json()['item_total_price'], 1050 * 25)
        down = self._update(5)
        self.assertEqual(down.json()['unit_price'], RETAIL)

    def test_client_cannot_override_unit_price(self):
        self.client.login(username='buyer-seller', password='secret12345')
        self._add(quantity=20, extra={'unit_price': 1, 'price': 1, 'total_price': 1})
        response = self._checkout()
        self.assertEqual(response.status_code, 302)
        item = OrderItem.objects.get()
        self.assertEqual(item.price_at_purchase, 1050)
        self.assertEqual(item.quantity, 20)
        self.assertEqual(Order.objects.get().total_price, 1050 * 20)

    def test_checkout_recalculates_price(self):
        self.client.login(username='buyer-seller', password='secret12345')
        self._add(quantity=30)
        ProductPriceTier.objects.filter(product=self.product, min_qty=30).delete()
        response = self._checkout()
        self.assertEqual(response.status_code, 302)
        item = OrderItem.objects.get()
        self.assertEqual(item.price_at_purchase, 1050)

    def test_order_item_gets_resolved_price(self):
        self.client.login(username='buyer-seller', password='secret12345')
        self._add(quantity=10)
        self._checkout()
        item = OrderItem.objects.get()
        self.assertEqual(item.price_at_purchase, 1200)
        self.assertNotEqual(item.price_at_purchase, self.product.price)

    def test_one_seller_cart_still_works(self):
        second = _product(
            title='Second same seller',
            slug='b2b-cart-second',
            article='B2B-CART-2',
        )
        self._add(quantity=1)
        response = self._add(product=second, quantity=1)
        self.assertEqual(response.status_code, 200)
        checkout = self._checkout()
        self.assertEqual(checkout.status_code, 302)
        self.assertEqual(OrderItem.objects.count(), 2)

    def test_phaeton_flow_stays_retail_without_b2b(self):
        virtual = CartManager.get_or_create_virtual_product({
            'sku': 'PH-B2B-1',
            'brand': 'Toyota',
            'price': 1000,
            'name': 'Phaeton filter',
            'supplier': Product.SUPPLIER_PHAETON,
        })
        self.assertEqual(virtual.supplier, Product.SUPPLIER_PHAETON)
        self.assertGreater(virtual.price, 0)
        response = self.client.post(
            reverse('orders:cart_add_api'),
            data=json.dumps({
                'product_id': virtual.id,
                'quantity': 2,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['cart_total'], virtual.price * 2)
        self.client.login(username='buyer-seller', password='secret12345')
        cart_page = self.client.get(reverse('orders:cart'))
        self.assertEqual(cart_page.status_code, 200)
        self.assertContains(cart_page, virtual.title)
        self.assertEqual(cart_page.context['cart_total'], virtual.price * 2)

    def test_retail_checkout_still_works_for_guest(self):
        self._add(quantity=2)
        response = self._checkout()
        self.assertEqual(response.status_code, 302)
        item = OrderItem.objects.get()
        self.assertEqual(item.price_at_purchase, RETAIL)
        self.assertEqual(Order.objects.get().total_price, RETAIL * 2)

    def test_stock_not_decremented_on_add(self):
        self.product.stock_qty = 12
        self.product.save(update_fields=['stock_qty'])
        self.client.login(username='buyer-seller', password='secret12345')
        self._add(quantity=10)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, 12)

    def test_add_over_stock_rejected(self):
        self.product.stock_qty = 4
        self.product.save(update_fields=['stock_qty'])
        response = self._add(quantity=5)
        self.assertEqual(response.status_code, 400)

    def test_price_on_request_seller_can_buy_with_tier(self):
        self.product.price = None
        self.product.price_on_request = True
        self.product.save(update_fields=['price', 'price_on_request'])
        guest = self._add(quantity=10)
        self.assertEqual(guest.status_code, 400)
        self.client.login(username='buyer-seller', password='secret12345')
        seller = self._add(quantity=10)
        self.assertEqual(seller.status_code, 200)
        self.assertEqual(seller.json()['cart_total'], 1200 * 10)

    def test_product_price_not_overwritten(self):
        self.client.login(username='buyer-seller', password='secret12345')
        self._add(quantity=20)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, RETAIL)

    def test_cost_price_not_in_cart_json(self):
        self.client.login(username='buyer-seller', password='secret12345')
        response = self._add(quantity=10)
        body = response.content.decode()
        self.assertNotIn('87654321', body)
        self.assertNotIn('cost_price', response.json())
