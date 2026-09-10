from django.test import RequestFactory, SimpleTestCase

from core.seo import canonical_url_for_path, robots_directive
from core.seo_views import STATIC_SITEMAP_PATHS


class AgPartsPublicSeoTargetTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_public_ag_parts_seller_page_is_indexable(self):
        request = self.factory.get('/seller/ag-parts/')

        self.assertEqual(robots_directive(request), 'index, follow')
        self.assertEqual(
            canonical_url_for_path(request.path),
            'https://zpt.kz/seller/ag-parts/',
        )

    def test_public_ag_parts_seller_page_is_in_static_sitemap(self):
        self.assertIn('/seller/ag-parts/', STATIC_SITEMAP_PATHS)
