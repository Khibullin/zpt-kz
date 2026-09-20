import json
import re
import uuid

from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from orders.constants import SESSION_CART_KEY
from orders.tests.test_manual_checkout import create_product
from orders.views import _sanitize_product_id


class SanitizeProductIdTests(SimpleTestCase):
    """Strict product_id parsing: separators OK, garbage rejected."""

    def test_plain_digits(self):
        self.assertEqual(_sanitize_product_id('2152'), 2152)
        self.assertEqual(_sanitize_product_id(2152), 2152)

    def test_space_separator(self):
        self.assertEqual(_sanitize_product_id('2 152'), 2152)

    def test_nbsp_separator(self):
        self.assertEqual(_sanitize_product_id('2\xa0152'), 2152)

    def test_narrow_nbsp_separator(self):
        self.assertEqual(_sanitize_product_id('2\u202f152'), 2152)

    def test_rejects_glued_garbage(self):
        self.assertIsNone(_sanitize_product_id('21abc52'))

    def test_rejects_decimal_and_sign_and_empty(self):
        self.assertIsNone(_sanitize_product_id('2.152'))
        self.assertIsNone(_sanitize_product_id('-2152'))
        self.assertIsNone(_sanitize_product_id(''))
        self.assertIsNone(_sanitize_product_id(None))
        self.assertIsNone(_sanitize_product_id(0))
        self.assertIsNone(_sanitize_product_id(-3))


@override_settings(
    USE_THOUSAND_SEPARATOR=True,
    NUMBER_GROUPING=3,
    LANGUAGE_CODE='ru-ru',
)
class CartAddProductIdLocalizationTests(TestCase):
    """Regression: localized product.id in HTML must not break add-to-cart."""

    def setUp(self):
        self.client = Client()
        self.high_pk = 2152

    def _make_product(self, *, pk, seller_name='AG Parts', **kwargs):
        kwargs.setdefault('article', f'LOC-{uuid.uuid4().hex[:8]}')
        kwargs.setdefault('seller_name', seller_name)
        kwargs.setdefault('title', f'Test product {pk}')
        return create_product(id=pk, **kwargs)

    def _cart_qty(self, product_id):
        cart = self.client.session.get(SESSION_CART_KEY, {}) or {}
        return int(cart.get(str(product_id), 0))

    def _add(self, **payload):
        return self.client.post(
            reverse('orders:cart_add_api'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_buy_controls_render_unlocalized_product_id(self):
        product = self._make_product(pk=self.high_pk)

        html = render_to_string(
            'catalog/includes/product_buy_controls.html',
            {'product': product},
        )

        self.assertIn(f'data-product-id="{self.high_pk}"', html)
        self.assertNotIn('\xa0', html)
        self.assertNotRegex(html, r'data-product-id="\d[\s\xa0]\d+"')

    def test_add_by_correct_high_pk_succeeds_and_keeps_quantity(self):
        product = self._make_product(pk=self.high_pk)

        response = self._add(product_id=product.id, quantity=3, mode='retail')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['success'])
        self.assertEqual(payload['product_id'], product.id)
        self.assertEqual(payload['cart_count'], 3)
        self.assertEqual(payload['total_items'], 3)
        self.assertEqual(self._cart_qty(product.id), 3)

    def test_one_request_adds_once_not_twice(self):
        product = self._make_product(pk=self.high_pk + 1, seller_name='Other Seller')

        response = self._add(product_id=product.id, quantity=1, mode='retail')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['cart_count'], 1)
        self.assertEqual(self._cart_qty(product.id), 1)

        response2 = self._add(product_id=product.id, quantity=1, mode='retail')
        self.assertEqual(response2.status_code, 200)
        self.assertEqual(response2.json()['cart_count'], 2)
        self.assertEqual(self._cart_qty(product.id), 2)

    def test_localized_id_nbsp_resolves_correct_product(self):
        product = self._make_product(pk=self.high_pk)
        response = self._add(product_id='2\xa0152', quantity=1, mode='retail')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['product_id'], product.id)
        self.assertEqual(self._cart_qty(product.id), 1)

    def test_localized_id_narrow_nbsp_resolves_correct_product(self):
        product = self._make_product(pk=self.high_pk)
        response = self._add(product_id='2\u202f152', quantity=1, mode='retail')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['product_id'], product.id)
        self.assertEqual(self._cart_qty(product.id), 1)

    def test_plain_string_id_resolves(self):
        product = self._make_product(pk=self.high_pk)
        response = self._add(product_id='2152', quantity=1, mode='retail')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['product_id'], product.id)

    def test_garbage_id_string_rejected_not_glued(self):
        product = self._make_product(pk=self.high_pk)
        response = self._add(product_id='21abc52', quantity=1, mode='retail')
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload.get('ok', True))
        self.assertEqual(self._cart_qty(product.id), 0)

    def test_wrong_truncated_id_without_article_returns_404_json(self):
        product = self._make_product(pk=self.high_pk)
        response = self._add(product_id=2, quantity=1, mode='retail')
        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertFalse(payload['ok'])
        self.assertFalse(payload['success'])
        self.assertIn('не найден', payload['message'].lower())
        self.assertEqual(self._cart_qty(product.id), 0)
        self.assertEqual(self._cart_qty(2), 0)

    def test_invalid_product_returns_structured_error(self):
        response = self._add(product_id=9_999_999, quantity=1, mode='retail')
        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertEqual(payload.get('ok'), False)
        self.assertEqual(payload.get('success'), False)
        self.assertTrue(payload.get('message') or payload.get('error'))

    def test_authenticated_user_add_persists(self):
        user = User.objects.create_user(username='buyer2152', password='secret12345')
        self.client.force_login(user)
        product = self._make_product(pk=self.high_pk + 2)

        response = self._add(product_id=product.id, quantity=2, mode='retail')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['cart_count'], 2)

        from orders.models import CartItem
        item = CartItem.objects.get(user=user, product_id=product.id)
        self.assertEqual(item.quantity, 2)

    def test_catalog_list_buy_controls_never_emit_localized_ids(self):
        product = self._make_product(pk=self.high_pk, price=5000)

        response = self.client.get(reverse('catalog_list'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        bad = re.findall(r'data-product-id="[^"]*[\s\xa0][^"]*"', html)
        self.assertEqual(bad, [])
        if f'data-product-id="{product.id}"' in html:
            self.assertNotIn(f'data-product-id="2\xa0152"', html)
