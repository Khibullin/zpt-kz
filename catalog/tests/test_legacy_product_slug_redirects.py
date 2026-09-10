from importlib import import_module

from django.test import SimpleTestCase
from django.urls import resolve

from catalog.legacy_product_urls import LEGACY_PRODUCT_SLUG_REDIRECTS
from catalog.views import product_detail


class LegacyProductSlugRedirectTests(SimpleTestCase):
    def test_all_legacy_product_slugs_redirect_permanently(self):
        self.assertEqual(len(LEGACY_PRODUCT_SLUG_REDIRECTS), 22)

        for old_slug, new_slug in LEGACY_PRODUCT_SLUG_REDIRECTS.items():
            with self.subTest(old_slug=old_slug):
                response = self.client.get(f'/{old_slug}/')
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response['Location'], f'/{new_slug}/')

    def test_migration_and_runtime_redirect_map_stay_in_sync(self):
        migration = import_module(
            'catalog.migrations.0030_cleanup_legacy_product_slugs'
        )
        migration_map = {
            old_slug: new_slug
            for _pk, old_slug, new_slug in migration.PRODUCT_SLUG_CHANGES
        }
        self.assertEqual(migration_map, LEGACY_PRODUCT_SLUG_REDIRECTS)

    def test_regular_product_slug_still_resolves_to_product_detail(self):
        match = resolve('/ordinary-product-slug/')
        self.assertIs(match.func, product_detail)
        self.assertEqual(match.kwargs['slug'], 'ordinary-product-slug')

    def test_numeric_product_route_is_unchanged(self):
        match = resolve('/12345/')
        self.assertIs(match.func, product_detail)
        self.assertEqual(match.kwargs['pk'], 12345)
