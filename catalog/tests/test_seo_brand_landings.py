from django.test import TestCase

from catalog.models import Brand, Category, Country, Product
from catalog.seo_landings import SEO_BRAND_LANDINGS


class SeoBrandLandingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        country = Country.objects.create(name='Китай SEO test')
        category = Category.objects.create(name='SEO test category')
        cls.brands = {}
        for slug, spec in SEO_BRAND_LANDINGS.items():
            brand = Brand.objects.create(country=country, name=spec['brand_name'])
            cls.brands[slug] = brand

        Product.objects.create(
            title='Тестовая запчасть Changan',
            slug='seo-changan-test-product',
            article='SEO-CHANGAN-1',
            price=1000,
            status='active',
            brand=cls.brands['changan'],
            category=category,
            seller_name='SEO Test Seller',
            whatsapp_number='+77010000000',
            description='Тестовое описание товара для индексируемой посадочной страницы.',
            main_image='products/seo-changan-test.jpg',
        )

    def test_reviewed_brand_landings_are_indexable_and_canonical(self):
        for slug, spec in SEO_BRAND_LANDINGS.items():
            with self.subTest(slug=slug):
                response = self.client.get(f'/avtozapchasti/{slug}/')
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, spec['h1'])
                self.assertContains(
                    response,
                    '<meta name="robots" content="index, follow">',
                    html=True,
                )
                self.assertContains(
                    response,
                    (
                        '<link rel="canonical" '
                        f'href="https://zpt.kz/avtozapchasti/{slug}/">'
                    ),
                    html=True,
                )

    def test_brand_landing_shows_matching_active_product(self):
        response = self.client.get('/avtozapchasti/changan/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Тестовая запчасть Changan')
        self.assertContains(response, '/seo-changan-test-product/')

    def test_unknown_brand_landing_is_404(self):
        response = self.client.get('/avtozapchasti/not-reviewed-brand/')

        self.assertEqual(response.status_code, 404)

    def test_static_sitemap_contains_all_reviewed_brand_landings(self):
        response = self.client.get('/sitemap-static.xml')

        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        for slug in SEO_BRAND_LANDINGS:
            self.assertIn(
                f'https://zpt.kz/avtozapchasti/{slug}/',
                body,
            )
