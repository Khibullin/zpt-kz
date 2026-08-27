import json

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import Product, ProductPriceTier, SellerProfile
from orders.constants import CART_MODE_RETAIL, CART_MODE_WHOLESALE, SESSION_CART_MODE_KEY
from orders.models import Order, OrderItem


RETAIL = 2500
WHOLESALE = 950
OTHER_WHOLESALE = 700


def _make_seller(username, name, phone, **kwargs):
    user = User.objects.create_user(username=username, password='secret12345')
    defaults = {
        'user': user,
        'name': name,
        'phone': phone,
        'city': 'Алматы',
        'address': 'г. Алматы, ул. Тестовая, 1',
        'wholesale_enabled': True,
        'wholesale_min_order_qty': 10,
    }
    defaults.update(kwargs)
    return SellerProfile.objects.create(**defaults)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    ORDER_ADMIN_EMAIL='orders-admin@test.local',
)
class PublicWholesaleCartTests(TestCase):
    def setUp(self):
        self.seller = _make_seller(
            'wholesale-cart-owner',
            'AG Parts',
            '77771360740',
        )
        self.other_seller = _make_seller(
            'other-wholesale-owner',
            'Other Shop',
            '77001112233',
        )
        self.products = [
            self._product(self.seller, title='Haval cabin', article='WH-H1', slug='wh-h1'),
            self._product(self.seller, title='Chery cabin', article='WH-C1', slug='wh-c1'),
            self._product(self.seller, title='Changan cabin', article='WH-G1', slug='wh-g1'),
            self._product(self.seller, title='Spark plug', article='WH-S1', slug='wh-s1'),
        ]
        for product in self.products:
            ProductPriceTier.objects.create(product=product, min_qty=1, price=WHOLESALE)
        self.other_product = self._product(
            self.other_seller,
            title='Other wholesale',
            article='WH-O1',
            slug='wh-o1',
            phone='+77001112233',
        )
        ProductPriceTier.objects.create(
            product=self.other_product,
            min_qty=1,
            price=OTHER_WHOLESALE,
        )

    def _product(self, seller, **kwargs):
        phone = kwargs.pop('phone', '+77771360740')
        defaults = {
            'price': RETAIL,
            'seller_name': seller.name,
            'seller_profile': seller,
            'whatsapp_number': phone,
            'status': 'active',
            'publish_to_sellers': True,
            'city': 'Алматы',
        }
        defaults.update(kwargs)
        return Product.objects.create(**defaults)

    def _add(self, product, quantity=1, mode=CART_MODE_WHOLESALE):
        payload = {
            'product_id': product.id,
            'quantity': quantity,
        }
        if mode is not None:
            payload['mode'] = mode
        return self.client.post(
            reverse('orders:cart_add_api'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def _checkout_post(self):
        return self.client.post(
            reverse('orders:checkout'),
            data={
                'customer_name': 'Иван',
                'customer_phone': '+7 (701) 123-45-67',
                'delivery_method': Order.DELIVERY_COURIER,
                'courier_street': 'Абая',
                'courier_house': '10',
            },
        )

    def test_guest_wholesale_cart_uses_tier_price(self):
        response = self._add(self.products[0], quantity=3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['cart_total'], WHOLESALE * 3)
        self.assertEqual(
            self.client.session.get(SESSION_CART_MODE_KEY),
            CART_MODE_WHOLESALE,
        )
        cart = self.client.get(reverse('orders:cart'))
        self.assertEqual(cart.context['cart_total'], WHOLESALE * 3)
        self.assertEqual(cart.context['items'][0]['unit_price'], WHOLESALE)
        self.assertTrue(cart.context['is_wholesale_cart'])
        html = cart.content.decode('utf-8')
        self.assertIn('Добавьте ещё 7 шт. для оптового заказа', html)

    def test_retail_cart_stays_retail(self):
        response = self._add(self.products[0], quantity=3, mode=None)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['cart_total'], RETAIL * 3)
        self.assertEqual(
            self.client.session.get(SESSION_CART_MODE_KEY),
            CART_MODE_RETAIL,
        )
        cart = self.client.get(reverse('orders:cart'))
        self.assertEqual(cart.context['items'][0]['unit_price'], RETAIL)
        self.assertFalse(cart.context['is_wholesale_cart'])

    def test_cannot_mix_retail_and_wholesale(self):
        first = self._add(self.products[0], quantity=1, mode=None)
        self.assertEqual(first.status_code, 200)
        mixed = self._add(self.products[1], quantity=1, mode=CART_MODE_WHOLESALE)
        self.assertEqual(mixed.status_code, 409)
        self.assertIn('розничн', mixed.json()['message'].lower())

        self.client = self.client_class()
        first = self._add(self.products[0], quantity=1, mode=CART_MODE_WHOLESALE)
        self.assertEqual(first.status_code, 200)
        mixed = self._add(self.products[1], quantity=1, mode=None)
        self.assertEqual(mixed.status_code, 409)
        self.assertIn('оптов', mixed.json()['message'].lower())

    def test_cannot_add_other_seller(self):
        self._add(self.products[0], quantity=1)
        response = self._add(self.other_product, quantity=1)
        self.assertEqual(response.status_code, 409)
        self.assertIn('продавца', response.json()['message'])

    def test_mixed_qty_ten_allows_checkout(self):
        quantities = (2, 3, 2, 3)
        for product, qty in zip(self.products, quantities):
            response = self._add(product, quantity=qty)
            self.assertEqual(response.status_code, 200)
        cart = self.client.get(reverse('orders:cart'))
        self.assertTrue(cart.context['wholesale_status']['can_checkout'])
        self.assertEqual(cart.context['wholesale_status']['total_qty'], 10)
        checkout = self.client.get(reverse('orders:checkout'))
        self.assertEqual(checkout.status_code, 200)
        created = self._checkout_post()
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.total_price, WHOLESALE * 10)
        items = list(OrderItem.objects.filter(order=order).order_by('product_id'))
        self.assertEqual(len(items), 4)
        self.assertEqual(sum(item.quantity for item in items), 10)
        for item in items:
            self.assertEqual(item.price_at_purchase, WHOLESALE)

    def test_nine_items_block_checkout(self):
        self._add(self.products[0], quantity=9)
        cart = self.client.get(reverse('orders:cart'))
        self.assertFalse(cart.context['wholesale_status']['can_checkout'])
        self.assertEqual(cart.context['wholesale_status']['remaining'], 1)
        get_checkout = self.client.get(reverse('orders:checkout'))
        self.assertEqual(get_checkout.status_code, 302)
        self.assertEqual(get_checkout.url, reverse('orders:cart'))
        self.assertEqual(Order.objects.count(), 0)

    def test_post_checkout_cannot_bypass_minimum(self):
        self._add(self.products[0], quantity=9)
        response = self._checkout_post()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('orders:cart'))
        self.assertEqual(Order.objects.count(), 0)
        follow = self.client.get(reverse('orders:cart'))
        self.assertContains(follow, 'Добавьте ещё 1 шт. для оптового заказа')
