import json
import uuid

from django.contrib.messages import get_messages
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from catalog.models import Product
from orders.constants import SESSION_CART_KEY
from orders.models import Order, OrderItem
from orders.tests.test_manual_checkout import (
    create_product,
    ensure_seller_profile_for_product,
)
from orders.views import UNAVAILABLE_CART_MESSAGE


UNAVAILABLE_SHORT = 'Товар больше недоступен.'


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    ORDER_ADMIN_EMAIL='orders-admin@test.local',
)
class InactiveProductCartTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _unique_article(self, prefix):
        return f'{prefix}-{uuid.uuid4().hex[:8]}'

    def _add_by_pk(self, product, quantity=1):
        return self.client.post(
            reverse('orders:cart_add_api'),
            data=json.dumps({
                'product_id': product.id,
                'quantity': quantity,
            }),
            content_type='application/json',
        )

    def _add_by_article(self, product, quantity=1):
        return self.client.post(
            reverse('orders:cart_add_api'),
            data=json.dumps({
                'article': product.article,
                'quantity': quantity,
            }),
            content_type='application/json',
        )

    def _cart_product_ids(self):
        cart_data = self.client.session.get(SESSION_CART_KEY, {}) or {}
        ids = []
        for raw_id in cart_data.keys():
            try:
                ids.append(int(raw_id))
            except (TypeError, ValueError):
                continue
        return ids

    def _checkout_post(self):
        return self.client.post(
            reverse('orders:checkout'),
            data={
                'customer_name': 'Иван',
                'customer_phone': '+7 (701) 123-45-67',
                'delivery_method': Order.DELIVERY_PICKUP,
            },
        )

    def test_add_by_pk_active_succeeds(self):
        product = create_product(article=self._unique_article('PK-ACT'))
        response = self._add_by_pk(product)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['product_id'], product.id)
        self.assertIn(product.id, self._cart_product_ids())

    def test_add_by_pk_hidden_rejected(self):
        product = create_product(
            article=self._unique_article('PK-HID'),
            status='hidden',
        )
        response = self._add_by_pk(product)
        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertFalse(payload['success'])
        self.assertEqual(self._cart_product_ids(), [])

    def test_add_by_pk_sold_rejected(self):
        product = create_product(
            article=self._unique_article('PK-SOLD'),
            status='sold',
        )
        response = self._add_by_pk(product)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()['success'])
        self.assertEqual(self._cart_product_ids(), [])

    def test_add_by_pk_url_hidden_rejected(self):
        product = create_product(
            article=self._unique_article('PK-URL'),
            status='hidden',
        )
        response = self.client.post(
            reverse('orders:cart_add', args=[product.id]),
            data=json.dumps({'quantity': 1}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self._cart_product_ids(), [])

    def test_add_by_article_active_succeeds(self):
        product = create_product(article=self._unique_article('ART-ACT'))
        response = self._add_by_article(product)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['product_id'], product.id)
        self.assertIn(product.id, self._cart_product_ids())

    def test_add_by_article_hidden_rejected(self):
        product = create_product(
            article=self._unique_article('ART-HID'),
            status='hidden',
        )
        response = self._add_by_article(product)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()['success'])
        self.assertEqual(self._cart_product_ids(), [])

    def test_add_by_article_sold_rejected(self):
        product = create_product(
            article=self._unique_article('ART-SOLD'),
            status='sold',
        )
        response = self._add_by_article(product)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()['success'])
        self.assertEqual(self._cart_product_ids(), [])

    def test_add_by_article_skips_inactive_and_picks_active(self):
        article = self._unique_article('ART-MIX')
        hidden = create_product(
            title='Hidden twin',
            article=article,
            status='hidden',
            seller_name='Hidden Seller',
            whatsapp_number='+77001110001',
        )
        active = create_product(
            title='Active twin',
            article=article,
            status='active',
            seller_name='Active Seller',
            whatsapp_number='+77001110002',
        )
        response = self._add_by_article(hidden)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['product_id'], active.id)
        self.assertEqual(self._cart_product_ids(), [active.id])

    def test_quantity_update_hidden_rejected(self):
        product = create_product(
            article=self._unique_article('UPD-HID'),
            status='hidden',
        )
        response = self.client.post(
            reverse('orders:cart_update_quantity'),
            data=json.dumps({
                'product_id': product.id,
                'quantity': 2,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()['success'])

    def test_checkout_after_hidden_does_not_create_order(self):
        self._assert_checkout_drops_inactive('hidden')

    def test_checkout_after_sold_does_not_create_order(self):
        self._assert_checkout_drops_inactive('sold')

    def _assert_checkout_drops_inactive(self, new_status):
        product = create_product(
            article=self._unique_article(f'CHG-{new_status}'),
            price=5000,
        )
        ensure_seller_profile_for_product(product, overwrite=False)
        add_response = self._add_by_pk(product)
        self.assertEqual(add_response.status_code, 200)

        product.status = new_status
        product.save(update_fields=['status'])

        checkout_get = self.client.get(reverse('orders:checkout'))
        self.assertEqual(checkout_get.status_code, 302)
        msgs = [str(message) for message in get_messages(checkout_get.wsgi_request)]
        self.assertIn(UNAVAILABLE_CART_MESSAGE, msgs)
        self.assertEqual(Order.objects.count(), 0)

        checkout_post = self._checkout_post()
        self.assertEqual(checkout_post.status_code, 302)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(OrderItem.objects.count(), 0)

    def test_checkout_mixed_cart_drops_inactive_keeps_active(self):
        active = create_product(
            article=self._unique_article('MIX-ACT'),
            price=3000,
            title='Stay active',
        )
        later_hidden = create_product(
            article=self._unique_article('MIX-HID'),
            price=4000,
            title='Will hide',
            whatsapp_number='+77771234567',
        )
        ensure_seller_profile_for_product(active, overwrite=False)
        self._add_by_pk(active)
        self._add_by_pk(later_hidden)

        later_hidden.status = 'hidden'
        later_hidden.save(update_fields=['status'])

        checkout_get = self.client.get(reverse('orders:checkout'))
        self.assertEqual(checkout_get.status_code, 200)
        messages = list(checkout_get.context['messages'])
        self.assertTrue(any(UNAVAILABLE_CART_MESSAGE in str(msg) for msg in messages))
        item_ids = [item['product'].id for item in checkout_get.context['items']]
        self.assertEqual(item_ids, [active.id])

        response = self._checkout_post()
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(list(order.items.values_list('product_id', flat=True)), [active.id])
        self.assertEqual(order.total_price, 3000)

    def test_cart_page_removes_inactive_and_shows_message(self):
        product = create_product(article=self._unique_article('CART-HID'))
        self._add_by_pk(product)
        product.status = 'sold'
        product.save(update_fields=['status'])

        response = self.client.get(reverse('orders:cart'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, UNAVAILABLE_CART_MESSAGE)
        self.assertEqual(response.context['items'], [])
        self.assertEqual(self._cart_product_ids(), [])

    def test_active_checkout_still_works(self):
        product = create_product(
            article=self._unique_article('OK-ACT'),
            price=9280,
        )
        ensure_seller_profile_for_product(product, overwrite=False)
        self._add_by_pk(product)
        response = self._checkout_post()
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.total_price, 9280)
        self.assertEqual(OrderItem.objects.get().product_id, product.id)
        self.assertNotIn(UNAVAILABLE_SHORT, str(response.content))
