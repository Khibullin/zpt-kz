import csv
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from catalog.models import Brand, CarModel, Category, Country, Product, SellerProfile
from catalog.product_assistant import OpenAIEnrichment, preview_enrichment_for_product
from catalog.product_quality import detect_internal_research_text


DIRTY_TOKENS = (
    'ZPT',
    'поставщиков',
    'по данным поставщика',
    'Gemini',
    'ChatGPT',
    'WhatsApp Image',
    'отвергнут',
    'FitInPart',
)


def _seller(username='enrich-seller', name='AG Parts Preview', phone='77770000811'):
    user = User.objects.create_user(username=username, password='secret12345')
    return SellerProfile.objects.create(
        user=user,
        name=name,
        phone=phone,
        city='Алматы',
    )


def _product(**kwargs):
    defaults = {
        'title': 'Фильтр салонный',
        'price': 4200,
        'seller_name': 'AG Parts Preview',
        'whatsapp_number': '77770000811',
        'status': 'active',
        'article': 'PREV-100',
        'city': 'Алматы',
        'stock_qty': 7,
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


def _public_blob(row: dict) -> str:
    return ' '.join([
        str(row.get('suggested_title') or ''),
        str(row.get('suggested_brand') or ''),
        str(row.get('suggested_category') or ''),
        str(row.get('suggested_compatibility') or ''),
        str(row.get('suggested_engine_compatibility') or ''),
        str(row.get('suggested_oem_cross_references') or ''),
        str(row.get('suggested_description') or ''),
    ])


class ProductEnrichmentPreviewTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name='Китай Preview')
        self.brand = Brand.objects.create(country=self.country, name='Chery')
        self.model = CarModel.objects.create(brand=self.brand, name='Tiggo 7')
        self.category = Category.objects.create(name='Фильтры')
        self.seller = _seller()

    def test_dirty_research_does_not_leak_to_public_fields(self):
        product = _product(
            title='Фильтр салонный Chery',
            article='DIRTY-PREV-1',
            slug='dirty-prev-1',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            category=self.category,
            compatibility='',
            description='',
        )

        def fake_ai(article, local_fields):
            return OpenAIEnrichment(
                title='Фильтр, в справочнике ZPT нет',
                compatibility=(
                    'Chery Tiggo 7. CS85 есть у поставщиков, в справочнике ZPT нет.'
                ),
                description='ChatGPT / Gemini. По данным поставщика WhatsApp Image 1.jpg.',
                oem_cross_references='OEM: ABC123; отвергнут XYZ',
                research_notes=[{
                    'text': 'CS85 встречается во внешних источниках, но не подтверждён.',
                    'severity': 'warning',
                }],
                confidence='likely',
                sources=[{'title': 'FitInPart', 'url': 'https://example.com/fit'}],
            )

        row = preview_enrichment_for_product(product, openai_caller=fake_ai)
        public = _public_blob(row)
        for token in DIRTY_TOKENS:
            self.assertNotIn(token, public)
        self.assertFalse(detect_internal_research_text(public))
        self.assertTrue(any('CS85' in item['text'] for item in row['research_notes']))
        self.assertNotIn('research_notes', row['fields'])
        self.assertEqual(row['sources'][0]['title'], 'FitInPart')

    def test_unresolved_facts_are_not_invented(self):
        product = _product(
            title='',
            article='EMPTY-PREV-1',
            slug='empty-prev-1',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            compatibility='',
            description='',
            engine_compatibility='',
            oem_cross_references='',
        )

        def fake_ai(article, local_fields):
            return OpenAIEnrichment(
                title='',
                brand='',
                category='',
                compatibility='Toyota Camry 40',
                engine_compatibility='2.4',
                oem_cross_references='90915-YZZD2',
                description='',
                confidence='needs_verification',
            )

        row = preview_enrichment_for_product(product, openai_caller=fake_ai)
        self.assertEqual(row['suggested_compatibility'], '')
        self.assertEqual(row['suggested_engine_compatibility'], '')
        self.assertEqual(row['suggested_oem_cross_references'], '')
        self.assertEqual(row['suggested_brand'], '')
        self.assertEqual(row['suggested_description'], '')
        unresolved_fields = {item['field'] for item in row['unresolved_fields']}
        self.assertIn('compatibility', unresolved_fields)
        self.assertIn('brand', unresolved_fields)
        notes = ' '.join(item['text'] for item in row['research_notes'])
        self.assertIn('Toyota Camry 40', notes)
        self.assertNotIn('Toyota Camry 40', row['suggested_compatibility'])

    def test_existing_valid_values_are_retained_when_research_is_weaker(self):
        product = _product(
            title='Фильтр салонный Chery Tiggo 7',
            article='KEEP-PREV-1',
            slug='keep-prev-1',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            car_model=self.model,
            category=self.category,
            compatibility='Chery Tiggo 7',
            description='Салонный фильтр для Tiggo 7.',
            engine_compatibility='1.5 Turbo',
            oem_cross_references='F4J16-3707010',
        )

        def fake_ai(article, local_fields):
            return OpenAIEnrichment(
                title='Другое название',
                brand='Geely',
                category='Электрика',
                compatibility='Geely Coolray',
                engine_compatibility='1.5 TD',
                oem_cross_references='OTHER-OEM',
                description='Слабое AI описание',
                confidence='needs_verification',
            )

        row = preview_enrichment_for_product(product, openai_caller=fake_ai)
        self.assertEqual(row['suggested_title'], 'Фильтр салонный Chery Tiggo 7')
        self.assertEqual(row['suggested_compatibility'], 'Chery Tiggo 7')
        self.assertEqual(row['suggested_description'], 'Салонный фильтр для Tiggo 7.')
        self.assertEqual(row['suggested_engine_compatibility'], '1.5 Turbo')
        self.assertEqual(row['suggested_oem_cross_references'], 'F4J16-3707010')
        self.assertEqual(row['suggested_brand'], 'Chery')
        self.assertEqual(row['suggested_category'], 'Фильтры')
        notes = ' '.join(item['text'] for item in row['research_notes'])
        self.assertIn('Geely Coolray', notes)

    def test_command_does_not_write_product_or_catalog(self):
        product = _product(
            title='Фильтр воздушный',
            article='CMD-PREV-1',
            slug='cmd-prev-1',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            category=self.category,
            price=4200,
            status='active',
            stock_qty=7,
        )
        before = {
            'title': product.title,
            'price': product.price,
            'status': product.status,
            'stock_qty': product.stock_qty,
            'seller_profile_id': product.seller_profile_id,
            'seller_name': product.seller_name,
            'brand_id': product.brand_id,
            'category_id': product.category_id,
            'main_image': str(product.main_image or ''),
        }
        brands = Brand.objects.count()
        categories = Category.objects.count()
        models = CarModel.objects.count()
        products = Product.objects.count()

        def fake_ai(article, local_fields):
            return OpenAIEnrichment(
                title='Не сохранять это название',
                brand='NewGhostBrand',
                category='НоваяКатегорияXYZ',
                compatibility='CS85 есть у поставщиков, в справочнике ZPT нет.',
                description='Gemini notes',
                confidence='likely',
            )

        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                'catalog.product_assistant.call_openai_product_lookup',
                side_effect=fake_ai,
            ):
                call_command(
                    'preview_product_enrichment',
                    '--product-ids',
                    str(product.pk),
                    '--report',
                    tmp,
                )
            csv_path = Path(tmp) / 'product_enrichment_preview.csv'
            json_path = Path(tmp) / 'product_enrichment_preview.json'
            self.assertTrue(csv_path.exists())
            self.assertTrue(json_path.exists())
            with csv_path.open(encoding='utf-8', newline='') as handle:
                csv_row = next(csv.DictReader(handle))
            public = _public_blob(csv_row)
            for token in DIRTY_TOKENS:
                self.assertNotIn(token, public)
            self.assertEqual(str(csv_row['product_id']), str(product.pk))
            payload = json.loads(json_path.read_text(encoding='utf-8'))
            self.assertEqual(payload['summary']['total'], 1)
            self.assertEqual(payload['products'][0]['current_article'], 'CMD-PREV-1')

        product.refresh_from_db()
        self.assertEqual(product.title, before['title'])
        self.assertEqual(product.price, before['price'])
        self.assertEqual(product.status, before['status'])
        self.assertEqual(product.stock_qty, before['stock_qty'])
        self.assertEqual(product.seller_profile_id, before['seller_profile_id'])
        self.assertEqual(product.seller_name, before['seller_name'])
        self.assertEqual(product.brand_id, before['brand_id'])
        self.assertEqual(product.category_id, before['category_id'])
        self.assertEqual(str(product.main_image or ''), before['main_image'])
        self.assertEqual(Brand.objects.count(), brands)
        self.assertEqual(Category.objects.count(), categories)
        self.assertEqual(CarModel.objects.count(), models)
        self.assertEqual(Product.objects.count(), products)
        self.assertFalse(Brand.objects.filter(name='NewGhostBrand').exists())
        self.assertFalse(Category.objects.filter(name='НоваяКатегорияXYZ').exists())
        self.assertIn('web_search_used', csv_row)
        self.assertIn('source_count', csv_row)
        self.assertIn('evidence_notes', csv_row)
        self.assertIn('ai_used', csv_row)


class ProductEnrichmentEvidenceTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name='Китай Evidence')
        self.brand = Brand.objects.create(country=self.country, name='Chery')
        self.lexus = Brand.objects.create(country=self.country, name='Lexus')
        self.lifan = Brand.objects.create(country=self.country, name='LIFAN')
        self.model = CarModel.objects.create(brand=self.brand, name='Tiggo 7')
        self.category = Category.objects.create(name='Фильтры')
        self.seller = _seller('evidence-seller', 'AG Parts Evidence', '77770000812')

    def test_target_product_alone_cannot_produce_likely_for_missing_facts(self):
        product = _product(
            title='Фильтр воздушный',
            article='SELF-EVIDENCE-1',
            slug='self-evidence-1',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=None,
            category=self.category,
            compatibility='',
            engine_compatibility='',
            oem_cross_references='',
        )
        from catalog.product_assistant import find_products_by_article
        self.assertEqual(
            find_products_by_article(product.article, exclude_product_id=product.pk),
            [],
        )

        def fake_ai(article, local_fields, **kwargs):
            return OpenAIEnrichment(
                brand='Lexus',
                compatibility='LIFAN 320, 330, Smily New — 1.3 л',
                confidence='confirmed',
                web_search_used=False,
            )

        row = preview_enrichment_for_product(product, openai_caller=fake_ai)
        self.assertEqual(row['confidence'], 'needs_verification')
        self.assertNotIn(row['confidence'], {'likely', 'confirmed'})
        self.assertEqual(row['suggested_brand'], '')
        self.assertEqual(row['suggested_compatibility'], '')
        self.assertFalse(row['web_search_used'])
        self.assertEqual(row['source_count'], 0)

    def test_web_search_call_action_sources_are_captured(self):
        from catalog.product_assistant import _walk_openai_text
        payload = {
            'output': [
                {
                    'type': 'web_search_call',
                    'status': 'completed',
                    'action': {
                        'type': 'search',
                        'query': 'FAE1109160 filter',
                        'sources': [
                            {'url': 'https://example.com/a', 'title': 'Catalog A'},
                            {'url': 'https://example.com/b', 'title': 'Catalog B'},
                        ],
                    },
                },
                {
                    'type': 'web_search_call',
                    'action': {
                        'type': 'open_page',
                        'url': 'https://example.com/page',
                        'title': 'Opened page',
                    },
                },
                {
                    'type': 'web_search_call',
                    'action': {
                        'type': 'find_in_page',
                        'url': 'https://example.com/page#oem',
                    },
                },
                {
                    'type': 'message',
                    'content': [{
                        'type': 'output_text',
                        'text': '{"title":"x"}',
                        'annotations': [{
                            'type': 'url_citation',
                            'url': 'https://example.com/a',
                            'title': 'duplicate A',
                        }],
                    }],
                },
            ]
        }
        text, sources, web_search_used = _walk_openai_text(payload)
        self.assertTrue(web_search_used)
        urls = [item['url'] for item in sources]
        self.assertEqual(urls.count('https://example.com/a'), 1)
        self.assertIn('https://example.com/b', urls)
        self.assertIn('https://example.com/page', urls)
        self.assertIn('https://example.com/page#oem', urls)
        self.assertIn('{"title":"x"}', text)

    def test_no_web_search_sources_do_not_confirm_new_public_facts(self):
        product = _product(
            title='Фильтр',
            article='NO-SRC-1',
            slug='no-src-1',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=None,
            category=self.category,
            compatibility='',
        )

        def fake_ai(article, local_fields, **kwargs):
            return OpenAIEnrichment(
                brand='Chery',
                compatibility='Chery Tiggo 7',
                engine_compatibility='1.5 Turbo',
                oem_cross_references='28113-2S000',
                confidence='confirmed',
                web_search_used=False,
                sources=[],
            )

        row = preview_enrichment_for_product(product, openai_caller=fake_ai)
        self.assertEqual(row['confidence'], 'needs_verification')
        self.assertEqual(row['suggested_brand'], '')
        self.assertEqual(row['suggested_compatibility'], '')
        self.assertEqual(row['suggested_engine_compatibility'], '')
        self.assertEqual(row['suggested_oem_cross_references'], '')
        notes = ' '.join(item['text'] for item in row['research_notes'])
        self.assertIn('Chery Tiggo 7', notes)

    def test_lexus_lifan_is_brand_compatibility_conflict(self):
        product = _product(
            title='Фильтр воздушный',
            article='FAE1109160',
            slug='fae1109160',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=None,
            category=self.category,
            compatibility='',
        )

        def fake_ai(article, local_fields, **kwargs):
            return OpenAIEnrichment(
                brand='Lexus',
                compatibility='LIFAN 320, 330, Smily New — 1.3 л',
                confidence='likely',
                web_search_used=True,
                sources=[
                    {'title': 'One', 'url': 'https://example.com/one'},
                    {'title': 'Two', 'url': 'https://example.com/two'},
                ],
            )

        row = preview_enrichment_for_product(product, openai_caller=fake_ai)
        blob = ' '.join(
            item['text'] for item in (row['research_notes'] + row['evidence_notes'])
        )
        self.assertIn('BRAND_COMPATIBILITY_CONFLICT', blob)
        self.assertEqual(row['confidence'], 'needs_verification')
        self.assertEqual(row['suggested_brand'], '')
        self.assertNotIn(row['confidence'], {'likely', 'confirmed'})

    def test_air_filter_alias_maps_to_existing_filters_category(self):
        product = _product(
            title='Фильтр воздушный JAC',
            article='F081109111HD',
            slug='f081109111hd',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=None,
            category=None,
        )
        from catalog.product_assistant import _match_category
        self.assertEqual(
            _match_category('Воздушные фильтры').name,
            'Фильтры',
        )
        self.assertEqual(_match_category('Воздушный фильтр').pk, self.category.pk)

        def fake_ai(article, local_fields, **kwargs):
            return OpenAIEnrichment(
                category='Воздушные фильтры',
                confidence='likely',
            )

        before = Category.objects.count()
        row = preview_enrichment_for_product(product, openai_caller=fake_ai)
        self.assertEqual(row['suggested_category'], 'Фильтры')
        self.assertEqual(Category.objects.count(), before)

    def test_missing_carmodel_does_not_blank_confirmed_compatibility(self):
        product = _product(
            title='Фильтр воздушный Jetour',
            article='JET-X70-1',
            slug='jet-x70-1',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=None,
            category=self.category,
            compatibility='',
        )
        models_before = CarModel.objects.count()

        def fake_ai(article, local_fields, **kwargs):
            return OpenAIEnrichment(
                models=['X70 Plus', 'X90 Plus'],
                compatibility='Jetour X70 Plus / X90 Plus',
                confidence='confirmed',
                web_search_used=True,
                sources=[
                    {'title': 'Src1', 'url': 'https://example.com/jetour-1'},
                    {'title': 'Src2', 'url': 'https://example.com/jetour-2'},
                ],
            )

        row = preview_enrichment_for_product(product, openai_caller=fake_ai)
        self.assertEqual(row['suggested_compatibility'], 'Jetour X70 Plus / X90 Plus')
        self.assertEqual(CarModel.objects.count(), models_before)
        self.assertFalse(CarModel.objects.filter(name__icontains='X70 Plus').exists())
        unmatched = ' '.join(row.get('unmatched') or [])
        self.assertIn('X70 Plus', unmatched)
        self.assertTrue(any('CarModel' in item['text'] for item in row['research_notes']) or 'CarModel' in unmatched)

    def test_fragmented_oem_is_rejected(self):
        product = _product(
            title='Фильтр',
            article='FRAG-OEM-1',
            slug='frag-oem-1',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            category=self.category,
            oem_cross_references='',
        )

        def fake_ai(article, local_fields, **kwargs):
            return OpenAIEnrichment(
                oem_cross_references='AG 302 ECO SA SB 8147 A1003 A-1180 SB 3250 71-01286-SX',
                confidence='likely',
                web_search_used=True,
                sources=[
                    {'title': 'One', 'url': 'https://example.com/oem-1'},
                    {'title': 'Two', 'url': 'https://example.com/oem-2'},
                ],
            )

        row = preview_enrichment_for_product(product, openai_caller=fake_ai)
        self.assertEqual(row['suggested_oem_cross_references'], '')
        notes = ' '.join(item['text'] for item in row['research_notes'] + row['evidence_notes'])
        self.assertTrue('OEM_FRAGMENTED' in notes or 'AG' in notes)

    def test_valid_source_backed_oem_is_retained(self):
        product = _product(
            title='Фильтр воздушный',
            article='1109130U1510',
            slug='valid-oem-1',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            category=self.category,
            oem_cross_references='',
        )

        def fake_ai(article, local_fields, **kwargs):
            return OpenAIEnrichment(
                oem_cross_references='1109130U1510\nF081109111HD\nF08-1109111HD\n28113-2S000',
                confidence='likely',
                web_search_used=True,
                sources=[
                    {'title': 'JAC', 'url': 'https://example.com/1109130u1510'},
                    {'title': 'Cross', 'url': 'https://example.com/28113-2s000'},
                ],
            )

        row = preview_enrichment_for_product(product, openai_caller=fake_ai)
        oem = row['suggested_oem_cross_references']
        self.assertNotIn('1109130U1510', oem.split('\n'))
        self.assertIn('F081109111HD', oem)
        self.assertIn('F08-1109111HD', oem)
        self.assertIn('28113-2S000', oem)

    def test_preview_never_writes_catalog_rows(self):
        product = _product(
            title='Фильтр',
            article='WRITE-GUARD-1',
            slug='write-guard-1',
            seller_profile=self.seller,
            seller_name=self.seller.name,
            brand=self.brand,
            category=self.category,
            price=9999,
            status='hidden',
            stock_qty=3,
        )
        before = {
            'price': product.price,
            'status': product.status,
            'stock_qty': product.stock_qty,
            'seller_profile_id': product.seller_profile_id,
            'title': product.title,
        }
        counts = (
            Product.objects.count(),
            Brand.objects.count(),
            Category.objects.count(),
            CarModel.objects.count(),
        )

        def fake_ai(article, local_fields, **kwargs):
            return OpenAIEnrichment(
                brand='GhostBrand',
                category='GhostCategory',
                models=['Unknown X'],
                compatibility='Ghost Car',
                confidence='confirmed',
                web_search_used=True,
                sources=[
                    {'title': 'A', 'url': 'https://example.com/a'},
                    {'title': 'B', 'url': 'https://example.com/b'},
                ],
            )

        preview_enrichment_for_product(product, openai_caller=fake_ai)
        product.refresh_from_db()
        self.assertEqual(product.price, before['price'])
        self.assertEqual(product.status, before['status'])
        self.assertEqual(product.stock_qty, before['stock_qty'])
        self.assertEqual(product.seller_profile_id, before['seller_profile_id'])
        self.assertEqual(product.title, before['title'])
        self.assertEqual(
            (
                Product.objects.count(),
                Brand.objects.count(),
                Category.objects.count(),
                CarModel.objects.count(),
            ),
            counts,
        )
        self.assertFalse(Brand.objects.filter(name='GhostBrand').exists())
        self.assertFalse(Category.objects.filter(name='GhostCategory').exists())
        self.assertFalse(CarModel.objects.filter(name='Unknown X').exists())
