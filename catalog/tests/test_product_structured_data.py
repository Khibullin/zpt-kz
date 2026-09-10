import json
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase, override_settings

from catalog.models import Brand, Category, Country, Product
from catalog.templatetags.product_extras import product_json_ld


@override_settings(PUBLIC_BASE_URL='https://zpt.kz')
class ProductStructuredDataTests(SimpleTestCase):
    def _product(self, **overrides):
        data = {
            'slug': 'test-product',
            'pk': 1,
            'title': 'Масляный фильтр TEST 123',
            'article': 'TEST-123',
            'description': 'Подробное описание тестового товара для автомобиля.',
            'brand': SimpleNamespace(name='Chery'),
            'category': SimpleNamespace(name='Масляные фильтры'),
            'main_image': SimpleNamespace(url='/products/test.jpg'),
            'condition': 'new',
            'price': 5500,
            'price_on_request': False,
            'stock_qty': 4,
        }
        data.update(overrides)
        return SimpleNamespace(**data)

    def test_product_json_ld_contains_core_product_and_offer_fields(self):
        payload = str(product_json_ld(self._product()))
        data = json.loads(payload)

        self.assertEqual(data['@context'], 'https://schema.org')
        self.assertEqual(data['@type'], 'Product')
        self.assertEqual(data['url'], 'https://zpt.kz/test-product/')
        self.assertEqual(data['name'], 'Масляный фильтр TEST 123')
        self.assertEqual(data['sku'], 'TEST-123')
        self.assertEqual(data['brand']['name'], 'Chery')
        self.assertEqual(data['category'], 'Масляные фильтры')
        self.assertEqual(data['image'], ['https://zpt.kz/products/test.jpg'])
        self.assertEqual(data['offers']['priceCurrency'], 'KZT')
        self.assertEqual(data['offers']['price'], 5500)
        self.assertEqual(
            data['offers']['availability'],
            'https://schema.org/InStock',
        )

    def test_unknown_stock_does_not_claim_availability(self):
        data = json.loads(str(product_json_ld(self._product(stock_qty=None))))

        self.assertIn('offers', data)
        self.assertNotIn('availability', data['offers'])

    def test_zero_stock_is_out_of_stock(self):
        data = json.loads(str(product_json_ld(self._product(stock_qty=0))))

        self.assertEqual(
            data['offers']['availability'],
            'https://schema.org/OutOfStock',
        )

    def test_price_on_request_does_not_emit_offer_price(self):
        data = json.loads(str(product_json_ld(self._product(price_on_request=True))))

        self.assertNotIn('offers', data)

    def test_legacy_slug_uses_public_canonical_alias(self):
        data = json.loads(str(product_json_ld(self._product(slug='audi', pk=1981))))

        self.assertEqual(
            data['url'],
            'https://zpt.kz/peugeot-308-hu71151x/',
        )
        self.assertEqual(
            data['@id'],
            'https://zpt.kz/peugeot-308-hu71151x/#product',
        )

    def test_json_ld_escapes_script_breakout_characters(self):
        product = self._product(description='Безопасно </script><script>alert(1)</script>')
        payload = str(product_json_ld(product))

        self.assertNotIn('</script>', payload.lower())
        self.assertIn('\\u003C/script\\u003E', payload)
        data = json.loads(payload)
        self.assertEqual(
            data['description'],
            'Безопасно </script><script>alert(1)</script>',
        )


@override_settings(PUBLIC_BASE_URL='https://zpt.kz')
class ProductStructuredDataIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        country = Country.objects.create(name='Structured Data Test Country')
        brand = Brand.objects.create(country=country, name='Structured Data Test Brand')
        category = Category.objects.create(name='Structured Data Test Category')
        cls.product = Product.objects.create(
            title='Structured Data Public Product',
            slug='structured-data-public-product',
            article='SD-100',
            price=9900,
            condition='new',
            status='active',
            brand=brand,
            category=category,
            seller_name='Structured Data Seller',
            whatsapp_number='+77010000000',
            description='Подробное описание товара для интеграционной проверки JSON-LD.',
            main_image='products/structured-data-test.jpg',
            stock_qty=2,
        )

    def test_public_product_page_emits_product_json_ld(self):
        response = self.client.get('/structured-data-public-product/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<script type="application/ld+json">')
        self.assertContains(response, 'Structured Data Public Product')
        self.assertContains(response, 'https://zpt.kz/structured-data-public-product/')

    def test_home_page_does_not_emit_product_json_ld(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<script type="application/ld+json">')
