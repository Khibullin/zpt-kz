from django.test import SimpleTestCase
from django.urls import resolve

from catalog.legacy_product_urls import (
    LEGACY_PRODUCT_SLUG_REDIRECTS,
    canonical_product_alias,
)
from catalog.numeric_product_urls import numeric_product_entry
from catalog.views import product_detail


class LegacyProductSlugRedirectTests(SimpleTestCase):
    def test_all_legacy_product_slugs_redirect_permanently(self):
        self.assertEqual(len(LEGACY_PRODUCT_SLUG_REDIRECTS), 22)

        for old_slug, new_slug in LEGACY_PRODUCT_SLUG_REDIRECTS.items():
            with self.subTest(old_slug=old_slug):
                response = self.client.get(f'/{old_slug}/')
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response['Location'], f'/{new_slug}/')

    def test_all_canonical_aliases_are_reserved_before_generic_slug_route(self):
        for old_slug, new_slug in LEGACY_PRODUCT_SLUG_REDIRECTS.items():
            with self.subTest(new_slug=new_slug):
                match = resolve(f'/{new_slug}/')
                self.assertIs(match.func, canonical_product_alias)
                self.assertEqual(match.kwargs['stored_slug'], old_slug)
                self.assertEqual(match.kwargs['new_slug'], new_slug)

    def test_regular_product_slug_still_resolves_to_product_detail(self):
        match = resolve('/ordinary-product-slug/')
        self.assertIs(match.func, product_detail)
        self.assertEqual(match.kwargs['slug'], 'ordinary-product-slug')

    def test_numeric_product_route_uses_safe_numeric_resolver(self):
        match = resolve('/12345/')
        self.assertIs(match.func, numeric_product_entry)
        self.assertEqual(match.kwargs['pk'], 12345)
