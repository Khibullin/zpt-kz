from django.test import TestCase

from catalog.models import Brand, Category, Country, Product


class SeoOilFilterLandingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        country = Country.objects.create(name='SEO Oil Test Country')
        brand = Brand.objects.create(country=country, name='SEO Oil Test Brand')
        category = Category.objects.create(name='SEO Oil Test Category')

        Product.objects.create(
            title='Масляный фильтр SEO TEST 123',
            slug='maslyanyy-filtr-seo-test-123',
            article='SEO-OIL-123',
            price=1500,
            status='active',
            brand=brand,
            category=category,
            seller_name='SEO Test Seller',
            whatsapp_number='+77010000000',
            description='Подробное тестовое описание масляного фильтра для автомобиля.',
            main_image='products/seo-oil-test.jpg',
        )
        Product.objects.create(
            title='Воздушный фильтр SEO TEST 456',
            slug='air-filter-seo-test-456',
            article='SEO-AIR-456',
            price=1600,
            status='active',
            brand=brand,
            category=category,
            seller_name='SEO Test Seller',
            whatsapp_number='+77010000000',
            description='Подробное тестовое описание воздушного фильтра для автомобиля.',
            main_image='products/seo-air-test.jpg',
        )

    def test_oil_filter_landing_is_indexable_and_canonical(self):
        response = self.client.get('/avtozapchasti/maslyanye-filtry/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Масляные фильтры для автомобилей в Казахстане',
        )
        self.assertContains(
            response,
            '<meta name="robots" content="index, follow">',
            html=True,
        )
        self.assertContains(
            response,
            (
                '<link rel="canonical" '
                'href="https://zpt.kz/avtozapchasti/maslyanye-filtry/">'
            ),
            html=True,
        )

    def test_oil_filter_landing_shows_only_matching_products(self):
        response = self.client.get('/avtozapchasti/maslyanye-filtry/')

        self.assertContains(response, 'Масляный фильтр SEO TEST 123')
        self.assertNotContains(response, 'Воздушный фильтр SEO TEST 456')
        self.assertContains(response, '/maslyanyy-filtr-seo-test-123/')

    def test_static_sitemap_contains_oil_filter_landing(self):
        response = self.client.get('/sitemap-static.xml')

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'https://zpt.kz/avtozapchasti/maslyanye-filtry/',
            response.content.decode('utf-8'),
        )
