from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from catalog.models import Brand, Category, Country, Product
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

    def test_render_origin_is_noindex_follow_with_zpt_canonical(self):
        request = self.factory.get(
            '/test-part/',
            HTTP_HOST='zpt-kz-backend.onrender.com',
        )
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

    def test_public_catalog_filters_and_pagination_are_noindex(self):
        cases = (
            '/parts-sellers/?city=Алматы',
            '/parts-sellers/?page=2',
            '/catalog/services/?service=Диагностика',
            '/catalog/services/?page=2',
        )
        for url in cases:
            with self.subTest(url=url):
                self.assertEqual(
                    robots_directive(self.factory.get(url)),
                    'noindex, follow',
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
    def _ready_product(self, **overrides):
        country, _ = Country.objects.get_or_create(name='SEO Test Country')
        brand, _ = Brand.objects.get_or_create(country=country, name='SEO Test Brand')
        category, _ = Category.objects.get_or_create(name='SEO Test Category')
        data = {
            'title': 'SEO ready test part',
            'slug': 'seo-ready-test-part',
            'article': 'SEO-001',
            'seller_name': 'Test seller',
            'whatsapp_number': '+77010000000',
            'status': 'active',
            'brand': brand,
            'category': category,
            'main_image': 'products/seo-test.jpg',
            'description': (
                'Подробное описание товара для безопасной индексации '
                'поисковыми системами Казахстана.'
            ),
        }
        data.update(overrides)
        return Product.objects.create(**data)

    def test_robots_txt_is_public_and_references_sitemap(self):
        response = self.client.get('/robots.txt')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/plain'))
        body = response.content.decode('utf-8')
        self.assertIn('Disallow: /admin/', body)
        self.assertIn('Disallow: /api/', body)
        self.assertIn('Allow: /static/', body)
        self.assertIn('Sitemap: https://zpt.kz/sitemap.xml', body)

    @override_settings(SEO_PRODUCT_SITEMAP_ENABLED=False)
    def test_sitemap_index_can_explicitly_disable_products(self):
        response = self.client.get('/sitemap.xml')

        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('https://zpt.kz/sitemap-static.xml', body)
        self.assertNotIn('https://zpt.kz/sitemap-products.xml', body)

    def test_initial_static_sitemap_is_deliberately_small(self):
        response = self.client.get('/sitemap-static.xml')

        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        for url in (
            'https://zpt.kz/',
            'https://zpt.kz/request-parts/',
            'https://zpt.kz/request-parts/guide/',
            'https://zpt.kz/request-parts/faq/',
            'https://zpt.kz/prodavat/',
        ):
            self.assertIn(url, body)
        self.assertNotIn('https://zpt.kz/catalog/services/', body)
        self.assertNotIn('https://zpt.kz/parts-sellers/', body)

    @override_settings(SEO_PRODUCT_SITEMAP_ENABLED=True)
    def test_sitemap_index_adds_products_when_enabled(self):
        response = self.client.get('/sitemap.xml')

        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('https://zpt.kz/sitemap-static.xml', body)
        self.assertIn('https://zpt.kz/sitemap-products.xml', body)

    @override_settings(SEO_PRODUCT_SITEMAP_ENABLED=False)
    def test_product_sitemap_is_empty_when_explicitly_disabled(self):
        self._ready_product(slug='active-test-part')

        response = self.client.get('/sitemap-products.xml')

        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertNotIn('active-test-part', body)

    @override_settings(SEO_PRODUCT_SITEMAP_ENABLED=True)
    def test_enabled_product_sitemap_contains_only_ready_active_products(self):
        self._ready_product(slug='active-test-part')
        self._ready_product(slug='hidden-test-part', status='hidden')
        no_slug = self._ready_product(slug='temporary-no-slug')
        Product.objects.filter(pk=no_slug.pk).update(slug='')
        self._ready_product(slug='short-description', description='Слишком коротко')
        self._ready_product(slug='no-image', main_image='')
        self._ready_product(slug='no-brand', brand=None)

        response = self.client.get('/sitemap-products.xml')

        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('https://zpt.kz/active-test-part/', body)
        self.assertNotIn('hidden-test-part', body)
        self.assertNotIn('temporary-no-slug', body)
        self.assertNotIn('short-description', body)
        self.assertNotIn('no-image', body)
        self.assertNotIn('no-brand', body)

    @override_settings(SEO_PRODUCT_SITEMAP_ENABLED=True)
    def test_product_sitemap_uses_canonical_alias_for_legacy_slug(self):
        self._ready_product(
            slug='audi',
            title='Масляный фильтр Peugeot 308 HU71151X',
            article='HU71151X',
        )

        response = self.client.get('/sitemap-products.xml')

        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('https://zpt.kz/peugeot-308-hu71151x/', body)
        self.assertNotIn('<loc>https://zpt.kz/audi/</loc>', body)

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
