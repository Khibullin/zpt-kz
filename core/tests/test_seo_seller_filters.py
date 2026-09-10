from types import SimpleNamespace

from django.test import RequestFactory, SimpleTestCase

from core.seo import robots_directive


class SellerStorefrontSeoTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, path, url_name, query=None):
        request = self.factory.get(path, data=query or {})
        request.resolver_match = SimpleNamespace(url_name=url_name)
        return request

    def test_clean_public_seller_profile_is_indexable(self):
        request = self._request('/seller/ag-parts/', 'public_seller_profile')
        self.assertEqual(robots_directive(request), 'index, follow')

    def test_public_seller_filters_are_noindex_follow(self):
        for key, value in {
            'q_seller': 'filter',
            'category': '26',
            'brand': '69',
            'model': '10',
            'page': '2',
        }.items():
            with self.subTest(key=key):
                request = self._request(
                    '/seller/ag-parts/',
                    'public_seller_profile',
                    {key: value},
                )
                self.assertEqual(robots_directive(request), 'noindex, follow')

    def test_tracking_parameter_does_not_noindex_clean_storefront(self):
        request = self._request(
            '/seller/ag-parts/',
            'public_seller_profile',
            {'utm_source': 'google'},
        )
        self.assertEqual(robots_directive(request), 'index, follow')

    def test_wholesale_filters_are_noindex_follow(self):
        for key, value in {
            'q': 'filter',
            'brand': '69',
            'type': 'stock',
            'page': '2',
        }.items():
            with self.subTest(key=key):
                request = self._request(
                    '/seller/ag-parts/wholesale/',
                    'public_seller_wholesale',
                    {key: value},
                )
                self.assertEqual(robots_directive(request), 'noindex, follow')

    def test_downloadable_wholesale_price_is_noindex_nofollow(self):
        request = self._request(
            '/seller/ag-parts/wholesale/price.xlsx',
            'public_seller_wholesale_price',
        )
        self.assertEqual(robots_directive(request), 'noindex, nofollow')
