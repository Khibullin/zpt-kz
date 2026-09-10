from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from catalog.models import Product
from core.seo import canonical_url_for_path, robots_directive, seo_context
from core.seo_middleware import SeoRobotsHeaderMiddleware


class SeoPolicyTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_catalog_filter_is_noindex_follow_and_canonical_home(self):
        request = self.factory.get('/?brand=63&utm_source=test')
        context = seo_context(request)

        self.assertEqual(context['seo_robots'], 'noindex, follow')
        self.assertEqual(context['seo_canonical_url'], 'https://zpt.kz/')

    def test_legacy_market_mount_is_noindex_follow(self):
        request = self.factory.get('/market/test-part/')
        context = seo_context(request)

        self.assertEqual(context['seo_robots'], 'noindex, follow')
        self.assertEqual(context['seo_canonical_url'], 'https://zpt.kz/test-part/')

    def test_tracking_parameter_on_product_does_not_force_noindex(self):
        request = self.factory.get('/test-part/?utm_source=google')

        self.assertEqual(robots_directive(request), 'index, follow')
        self.assertEqual(
            canonical_url_for_path(request.path),
            'https://zpt.kz/test-part/',
        )

    def test_request_parts_prefill_is_noindex_but_ad_tracking_is_not(self):
        prefilled = self.factory.get('/request-parts/?transport=car&brand=Toyota')
        ads_click = self.factory.get('/request-parts/?gclid=test&wbraid=test')

        self.assertEqual(robots_directive(prefilled), 'noindex, follow')
        self.assertEqual(robots_directive(ads_click), 'index, follow')
        self.assertEqual(
            canonical_url_for_path(prefilled.path),
            'https://zpt.kz/request-parts/',
        )

    def test_market_product_path_canonicalizes_to_root_product_path(self):
        self.assertEqual(
            canonical_url_for_path('/market/test-part/'),
            'https://zpt.kz/test-part/',
        )

    def test_technical_paths_are_noindex_nofollow(self):
        for path in (
            '/admin/',
            '/api/countries/',
            '/marketing/',
            '/seller/login/',
            '/request-parts/cabinet/',
            '/my-request/123/00000000-0000-0000-0000-000000000000/',
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    robots_directive(self.factory.get(path)),
                    'noindex, nofollow',
                )

    def test_registration_and_cart_are_noindex_follow(self):
        for path in ('/seller/register/', '/cart/', '/feedback/'):
            with self.subTest(path=path):
                self.assertEqual(
                    robots_directive(self.factory.get(path)),
                    'noindex, follow',
                )

    def test_middleware_adds_x_robots_tag_only_to_noindex_pages(self):
        middleware = SeoRobotsHeaderMiddleware(lambda request: HttpResponse('ok'))

        filtered = middleware(self.factory.get('/?q=brake'))
        product = middleware(self.factory.get('/brake-pad/'))

        self.assertEqual(filtered['X-Robots-Tag'], 'noindex, follow')
        self.assertNotIn('X-Robots-Tag', product)


class SeoEndpointTests(TestCase):
    def test_robots_txt_is_public_and_references_sitemap(self):
        response = self.client.get('/robots.txt')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/plain'))
        body = response.content.decode('utf-8')
        self.assertIn('Disallow: /admin/', body)
        self.assertIn('Disallow: /api/', body)
        self.assertIn('Allow: /static/', body)
        self.assertIn('Sitemap: https://zpt.kz/sitemap.xml', body)

    def test_sitemap_index_references_static_and_products(self):
        response = self.client.get('/sitemap.xml')

        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('https://zpt.kz/sitemap-static.xml', body)
        self.assertIn('https://zpt.kz/sitemap-products.xml', body)

    def test_product_sitemap_contains_only_active_slug_products(self):
        Product.objects.create(
            title='Active test part',
            slug='active-test-part',
            seller_name='Test seller',
            whatsapp_number='+77010000000',
            status='active',
        )
        Product.objects.create(
            title='Hidden test part',
            slug='hidden-test-part',
            seller_name='Test seller',
            whatsapp_number='+77010000000',
            status='hidden',
        )
        no_slug = Product.objects.create(
            title='No slug test part',
            slug='temporary-no-slug',
            seller_name='Test seller',
            whatsapp_number='+77010000000',
            status='active',
        )
        Product.objects.filter(pk=no_slug.pk).update(slug='')

        response = self.client.get('/sitemap-products.xml')

        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('https://zpt.kz/active-test-part/', body)
        self.assertNotIn('hidden-test-part', body)
        self.assertNotIn('temporary-no-slug', body)

    def test_home_template_outputs_indexable_canonical_metadata(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<meta name="robots" content="index, follow">', html=True)
        self.assertContains(response, '<link rel="canonical" href="https://zpt.kz/">', html=True)

    def test_filtered_home_outputs_noindex_and_x_robots_tag(self):
        response = self.client.get('/?q=brake')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Robots-Tag'], 'noindex, follow')
        self.assertContains(response, '<meta name="robots" content="noindex, follow">', html=True)
        self.assertContains(response, '<link rel="canonical" href="https://zpt.kz/">', html=True)

    def test_market_home_is_noindex_with_root_canonical(self):
        response = self.client.get('/market/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Robots-Tag'], 'noindex, follow')
        self.assertContains(response, '<meta name="robots" content="noindex, follow">', html=True)
        self.assertContains(response, '<link rel="canonical" href="https://zpt.kz/">', html=True)
