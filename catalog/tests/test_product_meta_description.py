from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase, override_settings

from catalog.models import Brand, Category, Country, Product
from catalog.templatetags.product_extras import public_product_meta_description


class ProductMetaDescriptionUnitTests(SimpleTestCase):
    def test_description_uses_public_facts_and_stays_compact(self):
        product = SimpleNamespace(
            title='Свеча зажигания Chery Tiggo 7',
            article='F4J163707010',
            brand=SimpleNamespace(name='Chery'),
        )

        description = public_product_meta_description(product)

        self.assertIn('Свеча зажигания Chery Tiggo 7', description)
        self.assertIn('Арт. F4J163707010', description)
        self.assertNotIn('Марка Chery', description)
        self.assertIn('Купить в Казахстане на ZPT.KZ', description)
        self.assertLessEqual(len(description), 160)

    def test_brand_is_added_when_title_does_not_name_it(self):
        product = SimpleNamespace(
            title='Воздушный фильтр 110912U2210',
            article='110912U2210',
            brand=SimpleNamespace(name='JAC'),
        )

        description = public_product_meta_description(product)

        self.assertIn('Марка JAC.', description)
        self.assertLessEqual(len(description), 160)


@override_settings(PUBLIC_BASE_URL='https://zpt.kz')
class ProductMetaDescriptionIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        country = Country.objects.create(name='Meta Description Test Country')
        brand = Brand.objects.create(country=country, name='JAC')
        category = Category.objects.create(name='Meta Description Test Category')
        cls.product = Product.objects.create(
            title='Воздушный фильтр JAC S3',
            slug='meta-description-product',
            article='110912U2210',
            price=5000,
            condition='new',
            status='active',
            brand=brand,
            category=category,
            seller_name='Meta Test Seller',
            whatsapp_number='+77010000000',
            description='Подробное описание товара для проверки мета-тегов.',
            main_image='products/meta-description-test.jpg',
        )

    def test_product_page_has_unique_meta_description_and_open_graph_copy(self):
        response = self.client.get('/meta-description-product/')
        expected = public_product_meta_description(self.product)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<meta name="description" content="{expected}">',
            html=True,
        )
        self.assertContains(
            response,
            '<meta property="og:type" content="product">',
            html=True,
        )
        self.assertContains(
            response,
            '<meta property="og:title" content="Воздушный фильтр JAC S3 — ZPT.KZ">',
            html=True,
        )
        self.assertContains(
            response,
            f'<meta property="og:description" content="{expected}">',
            html=True,
        )

    def test_home_keeps_site_level_meta_description(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<meta name="description" content="Автозапчасти',
        )
        self.assertContains(
            response,
            '<meta property="og:type" content="website">',
            html=True,
        )
