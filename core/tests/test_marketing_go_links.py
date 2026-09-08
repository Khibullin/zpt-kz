from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from catalog.models import SellerProfile
from core.go_views import GO_DESTINATIONS, MARKET_ADD_PRODUCT, MARKET_SELLER_LOGIN


class MarketingGoLinkTests(TestCase):
    EXPECTED_ROUTES = {
        'requests': '/request-parts/cabinet/',
        'add-product': '/market/seller/add/',
        'wholesale': '/market/?offer=wholesale&all=1',
        'sellers': '/parts-sellers/',
        'help': '/request-parts/help/',
    }

    def _next_query(self, url):
        parsed = urlparse(url)
        return parsed.path, parse_qs(parsed.query)

    def test_every_allowlisted_route_redirects_to_the_fixed_target(self):
        self.assertEqual(dict(GO_DESTINATIONS), self.EXPECTED_ROUTES)
        session_sensitive = {'add-product'}
        for destination, target in self.EXPECTED_ROUTES.items():
            if destination in session_sensitive:
                continue
            with self.subTest(destination=destination):
                response = self.client.get(f'/go/{destination}/')
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, target)
                self.assertEqual(response['Cache-Control'], 'no-store')
                self.assertFalse(response.url.startswith('//'))

    def test_anonymous_add_product_go_link_redirects_to_seller_login(self):
        response = self.client.get('/go/add-product/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Cache-Control'], 'no-store')
        path, query = self._next_query(response.url)
        self.assertEqual(path, MARKET_SELLER_LOGIN)
        self.assertEqual(query.get('next'), [MARKET_ADD_PRODUCT])
        self.assertNotEqual(response.url, MARKET_ADD_PRODUCT)

    def test_admin_without_seller_profile_gets_seller_login_not_404(self):
        admin = User.objects.create_superuser(
            'go-admin',
            'go-admin@test.local',
            'AdminPass123',
        )
        self.client.force_login(admin)
        response = self.client.get('/go/add-product/')
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response.status_code, 404)
        path, query = self._next_query(response.url)
        self.assertEqual(path, MARKET_SELLER_LOGIN)
        self.assertEqual(query.get('next'), [MARKET_ADD_PRODUCT])
        login_page = self.client.get(response.url)
        self.assertEqual(login_page.status_code, 200)
        self.assertContains(login_page, 'Вход продавца')

    def test_authenticated_seller_with_profile_goes_to_add_product(self):
        user = User.objects.create_user('77015550901', password='SellerPass123')
        SellerProfile.objects.create(
            user=user,
            name='Go Shop',
            phone='77015550901',
            city='Алматы',
        )
        self.client.force_login(user)
        response = self.client.get('/go/add-product/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, MARKET_ADD_PRODUCT)
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_named_route_uses_slug_destination(self):
        url = reverse('marketing_go_redirect', kwargs={'destination': 'requests'})
        self.assertEqual(url, '/go/requests/')

    def test_unknown_destination_returns_404(self):
        response = self.client.get('/go/unknown-destination/')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Location', response)

    def test_post_does_not_redirect(self):
        response = self.client.post('/go/requests/')
        self.assertEqual(response.status_code, 405)
        self.assertNotIn('Location', response)

    def test_help_post_does_not_redirect(self):
        response = self.client.post('/go/help/')
        self.assertEqual(response.status_code, 405)
        self.assertNotIn('Location', response)

    def test_head_redirects_with_no_store(self):
        response = self.client.head('/go/sellers/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/parts-sellers/')
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_client_query_cannot_create_an_external_redirect(self):
        response = self.client.get(
            '/go/requests/',
            {
                'next': 'https://evil.example/',
                'url': 'https://evil.example/phish',
                'redirect': '//evil.example',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/request-parts/cabinet/')
        self.assertNotIn('evil', response.url.lower())
        self.assertFalse(response.url.startswith('http'))

    def test_allowlist_targets_are_local_paths(self):
        for destination, target in GO_DESTINATIONS.items():
            with self.subTest(destination=destination):
                self.assertTrue(target.startswith('/'), destination)
                self.assertFalse(target.startswith('//'), destination)
                self.assertNotIn('://', target)
