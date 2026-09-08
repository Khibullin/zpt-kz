from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from catalog.models import SellerProfile
from core.go_views import MARKET_ADD_PRODUCT, MARKET_SELLER_LOGIN


SHOP_PASSWORD = 'ShopPass12345'


class SellerLoginNextRedirectTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            '77015550911',
            password=SHOP_PASSWORD,
        )
        SellerProfile.objects.create(
            user=self.user,
            name='Next Shop',
            phone='77015550911',
            city='Алматы',
        )

    def _login_payload(self, **extra):
        payload = {
            'username': '77015550911',
            'password': SHOP_PASSWORD,
        }
        payload.update(extra)
        return payload

    def test_get_login_keeps_safe_next_in_hidden_field(self):
        response = self.client.get(
            MARKET_SELLER_LOGIN,
            {'next': MARKET_ADD_PRODUCT},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="next"')
        self.assertContains(response, f'value="{MARKET_ADD_PRODUCT}"')

    def test_safe_next_redirects_to_add_product(self):
        response = self.client.post(
            MARKET_SELLER_LOGIN,
            self._login_payload(next=MARKET_ADD_PRODUCT),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, MARKET_ADD_PRODUCT)

    def test_external_next_falls_back_to_seller_dashboard(self):
        response = self.client.post(
            MARKET_SELLER_LOGIN,
            self._login_payload(next='https://evil.example/'),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('seller_dashboard'))
        self.assertNotIn('evil', response.url.lower())

    def test_login_without_next_goes_to_seller_dashboard(self):
        response = self.client.post(
            MARKET_SELLER_LOGIN,
            self._login_payload(),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('seller_dashboard'))
