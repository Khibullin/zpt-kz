import json
from io import StringIO
from types import SimpleNamespace

from django.core.management import call_command
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
        self.assertNotIn('brand', data)  # Chery is vehicle make, not part maker.
        self.assertEqual(data['category'], 'Масляные фильтры')
        self.assertEqual(data['image'], ['https://zpt.kz/products/test.jpg'])
        self.assertEqual(data['offers']['priceCurrency'], 'KZT')
        self.assertEqual(data['offers']['price'], 5500)
        self.assertEqual(
            data['offers']['availability'],
            'https://schema.org/InStock',
        )

    def test_missing_specific_category_uses_truthful_autoparts_fallback(self):
        data = json.loads(str(product_json_ld(self._product(category=None))))
        self.assertEqual(data['category'], 'Автозапчасти')

    def test_blank_specific_category_uses_autoparts_fallback(self):
        data = json.loads(str(product_json_ld(
            self._product(category=SimpleNamespace(name='   '))
        )))
        self.assertEqual(data['category'], 'Автозапчасти')

    def test_unverified_shipping_returns_and_reviews_are_not_invented(self):
        data = json.loads(str(product_json_ld(self._product())))
        self.assertNotIn('shippingDetails', data['offers'])
        self.assertNotIn('hasMerchantReturnPolicy', data['offers'])
        self.assertNotIn('review', data)
        self.assertNotIn('aggregateRating', data)

    def test_explicit_ag_parts_brand_is_not_confused_with_vehicle_make(self):
        product = self._product(title='Салонный фильтр AG Parts Chery Tiggo')
        data = json.loads(str(product_json_ld(product)))

        self.assertEqual(data['brand']['name'], 'AG Parts')

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
        common = {
            'price': 9900,
            'condition': 'new',
            'status': 'active',
            'brand': brand,
            'category': category,
            'seller_name': 'Structured Data Seller',
            'whatsapp_number': '+77010000000',
            'description': 'Подробное описание товара для интеграционной проверки JSON-LD.',
            'main_image': 'products/structured-data-test.jpg',
            'stock_qty': 2,
        }
        cls.product = Product.objects.create(
            title='Structured Data Public Product',
            slug='structured-data-public-product',
            article='SD-100',
            **common,
        )
        cls.legacy_product = Product.objects.create(
            title='Structured Data Legacy Product',
            slug='audi',
            article='HU71151X',
            **common,
        )
        cls.fitment_product = Product.objects.create(
            title='Масляный фильтр Chery Tiggo 7',
            slug='changan-cs55',
            article='4801012010',
            **common,
        )

    def test_product_article_in_h1_and_image_alt_without_duplication(self):
        response = self.client.get('/structured-data-public-product/')

        self.assertContains(response, 'Structured Data Public Product SD-100')
        self.assertContains(response, 'alt="Structured Data Public Product SD-100 — фото"')

    def test_public_product_page_emits_product_json_ld(self):
        response = self.client.get('/structured-data-public-product/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<script type="application/ld+json">')
        self.assertContains(response, 'Structured Data Public Product')
        self.assertContains(response, 'https://zpt.kz/structured-data-public-product/')

    def test_canonical_alias_page_also_emits_product_json_ld(self):
        response = self.client.get('/peugeot-308-hu71151x/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<script type="application/ld+json">')
        self.assertContains(response, 'Structured Data Legacy Product')
        self.assertContains(response, 'https://zpt.kz/peugeot-308-hu71151x/')

    def test_fitment_alias_page_emits_product_json_ld_and_product_meta(self):
        response = self.client.get('/masljanyi-filtr-4801012010/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<meta property="og:type" content="product">', html=True)
        self.assertContains(response, '<script type="application/ld+json">')
        self.assertContains(response, '<meta name="description"')
        self.assertContains(response, 'Арт. 4801012010')
        page = response.content.decode('utf-8')
        import re
        scripts = re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
        self.assertEqual(len(scripts), 1)
        data = json.loads(scripts[0])
        self.assertEqual(data['@type'], 'Product')
        self.assertEqual(data['url'], 'https://zpt.kz/masljanyi-filtr-4801012010/')
        self.assertEqual(data['sku'], '4801012010')
        self.assertNotIn('brand', data)  # car make is not component manufacturer

    def test_read_only_product_schema_audit_reports_unknown_stock_and_category(self):
        product = Product.objects.create(
            title='Сальник тестовый',
            slug='test-schema-audit-unknown',
            seller_name='SEO Testing Seller',
            whatsapp_number='+77010000000',
            status='active',
            price=1000,
            price_on_request=False,
            stock_qty=None,
            category=None,
        )
        output = StringIO()
        call_command('audit_product_schema', '--limit', '25', stdout=output)
        report = output.getvalue()
        self.assertIn('unknown_stock_count=1', report)
        self.assertIn('missing_specific_category_count=1', report)
        self.assertIn('/test-schema-audit-unknown/', report)
        self.assertIn('stock=unknown,specific_category=missing', report)
        product.refresh_from_db()
        self.assertIsNone(product.stock_qty)
        self.assertIsNone(product.category)

    def test_home_page_does_not_emit_product_json_ld(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<script type="application/ld+json">')
