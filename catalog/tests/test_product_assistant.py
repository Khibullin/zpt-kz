from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.article_utils import normalize_article
from catalog.forms import ProductForm
from catalog.models import Brand, CarModel, Category, Country, Product, SellerProfile
from catalog.product_assistant import (
    OpenAIEnrichment,
    _match_brand,
    _match_car_model,
    suggest_product_by_article,
)


def _seller(username='assistant-seller', name='Assistant Shop', phone='77770000111'):
    user = User.objects.create_user(username=username, password='secret12345')
    return SellerProfile.objects.create(
        user=user,
        name=name,
        phone=phone,
        city='Алматы',
    )


def _product(**kwargs):
    defaults = {
        'title': 'Фильтр масляный',
        'price': 1500,
        'seller_name': 'Assistant Shop',
        'whatsapp_number': '77770000111',
        'status': 'active',
        'article': 'F4J16-3707010',
        'city': 'Алматы',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class ArticleNormalizeTests(TestCase):
    def test_strips_separators_and_uppercases(self):
        self.assertEqual(normalize_article(' f4j16-3707010 '), 'F4J163707010')
        self.assertEqual(normalize_article('ABC.12/3'), 'ABC123')
        self.assertEqual(normalize_article(''), '')
        self.assertEqual(normalize_article(None), '')


class ProductAssistantLookupTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name='Китай AI')
        self.brand = Brand.objects.create(country=self.country, name='Chery')
        self.model = CarModel.objects.create(brand=self.brand, name='Tiggo 7')
        self.category = Category.objects.create(name='Фильтры AI')
        self.seller_a = _seller('seller-a', 'Shop A', '77770000001')
        self.seller_b = _seller('seller-b', 'Shop B', '77770000002')

    def test_exact_article_match(self):
        _product(
            title='Фильтр салонный Chery',
            article='OEM-555',
            slug='oem-555-a',
            seller_profile=self.seller_a,
            seller_name=self.seller_a.name,
            brand=self.brand,
            car_model=self.model,
            category=self.category,
            compatibility='Chery Tiggo 7',
            engine_compatibility='1.5 Turbo',
            oem_cross_references='OEM-555\nF4J16',
            description='Салонный фильтр',
        )
        result = suggest_product_by_article('OEM-555', openai_caller=lambda *args, **kwargs: None)
        self.assertTrue(result['ok'])
        self.assertEqual(result['match_count'], 1)
        self.assertEqual(result['fields']['title'], 'Фильтр салонный Chery')
        self.assertEqual(result['fields']['brand_id'], self.brand.pk)
        self.assertEqual(result['fields']['category_id'], self.category.pk)
        self.assertEqual(result['confidence'], 'confirmed')
        self.assertFalse(result['ai_used'])

    def test_same_article_from_different_sellers_does_not_break_search(self):
        _product(
            title='Свеча зажигания',
            article='SPARK-9',
            slug='spark-9-a',
            seller_profile=self.seller_a,
            seller_name=self.seller_a.name,
            brand=self.brand,
            description='Оригинал',
        )
        _product(
            title='Свеча зажигания',
            article='SPARK-9',
            slug='spark-9-b',
            seller_profile=self.seller_b,
            seller_name=self.seller_b.name,
            brand=self.brand,
            description='Аналог',
        )
        result = suggest_product_by_article('SPARK-9', openai_caller=lambda *args, **kwargs: None)
        self.assertTrue(result['ok'])
        self.assertEqual(result['match_count'], 2)
        self.assertEqual(result['fields']['title'], 'Свеча зажигания')
        self.assertEqual(result['fields']['brand_id'], self.brand.pk)

    def test_no_result(self):
        result = suggest_product_by_article(
            'UNKNOWN-ARTICLE-ZZZ',
            openai_caller=lambda *args, **kwargs: None,
        )
        self.assertTrue(result['ok'])
        self.assertEqual(result['match_count'], 0)
        self.assertIn('ничего не найдено', result['message'].lower())
        self.assertEqual(result['fields']['title'], '')

    def test_ai_unavailable_falls_back_to_zpt_data(self):
        _product(
            title='Амортизатор передний',
            article='ABS-100',
            slug='abs-100',
            seller_profile=self.seller_a,
            seller_name=self.seller_a.name,
            brand=self.brand,
        )

        def boom(*args, **kwargs):
            raise RuntimeError('openai down')

        result = suggest_product_by_article('ABS-100', openai_caller=boom)
        self.assertTrue(result['ok'])
        self.assertFalse(result['ai_used'])
        self.assertEqual(result['fields']['title'], 'Амортизатор передний')
        self.assertTrue(result['ai_error'])

    @override_settings(OPENAI_API_KEY='')
    def test_missing_openai_key_does_not_error(self):
        result = suggest_product_by_article('NO-KEY-1')
        self.assertTrue(result['ok'])
        self.assertFalse(result['ai_used'])
        self.assertEqual(result['match_count'], 0)

    def test_ai_does_not_create_catalog_records(self):
        category_count = Category.objects.count()
        brand_count = Brand.objects.count()
        model_count = CarModel.objects.count()

        def fake_ai(article, local_fields):
            return OpenAIEnrichment(
                title='Дворник зимний',
                category='Несуществующая категория XYZ',
                brand='UnknownBrandXYZ',
                models=['UnknownModelXYZ'],
                description='Текстовая рекомендация',
                confidence='likely',
            )

        result = suggest_product_by_article('NEW-ART-1', openai_caller=fake_ai)
        self.assertTrue(result['ok'])
        self.assertEqual(result['fields']['title'], 'Дворник зимний')
        self.assertIsNone(result['fields']['category_id'])
        self.assertIsNone(result['fields']['brand_id'])
        self.assertTrue(result['unmatched'])
        self.assertEqual(Category.objects.count(), category_count)
        self.assertEqual(Brand.objects.count(), brand_count)
        self.assertEqual(CarModel.objects.count(), model_count)


class ProductAssistantPublicFieldTests(TestCase):
    def test_ai_compatibility_strips_research_commentary(self):
        def fake_ai(article, local_fields):
            return OpenAIEnrichment(
                title='Свеча зажигания Changan CS75 Plus — D20T0120700',
                compatibility=(
                    'CS75 Plus, UNI-K. CS85 есть у поставщиков, в справочнике ZPT нет.'
                ),
                description='Свеча зажигания предназначена для воспламенения смеси.',
                research_notes=[{
                    'text': '1.4T / 1.5T имеют противоречивую применимость.',
                    'severity': 'warning',
                }],
                confidence='likely',
            )

        result = suggest_product_by_article('D20T0120700', openai_caller=fake_ai)
        compatibility = result['fields']['compatibility']
        self.assertIn('UNI-K', compatibility)
        self.assertNotIn('поставщиков', compatibility)
        self.assertNotIn('ZPT', compatibility)
        self.assertNotIn('справочнике', compatibility)
        notes = ' '.join(item['text'] for item in result['research_notes'])
        self.assertTrue('поставщиков' in notes or '1.4T' in notes)

    def test_research_notes_are_separate_from_fields(self):
        def fake_ai(article, local_fields):
            return OpenAIEnrichment(
                title='Свеча зажигания',
                compatibility='Changan CS75 Plus, UNI-K — 2.0T',
                research_notes=[{
                    'text': 'CS85 встречается во внешних источниках, но не подтверждён.',
                    'severity': 'info',
                }],
            )

        result = suggest_product_by_article('NOTE-1', openai_caller=fake_ai)
        self.assertTrue(any('CS85' in item['text'] for item in result['research_notes']))
        self.assertNotIn('research_notes', result['fields'])
        self.assertNotIn('внешних источниках', result['fields']['compatibility'])

    def test_dirty_local_compatibility_does_not_block_clean_ai(self):
        seller = _seller('dirty-local', 'Dirty Shop', '77770000901')
        _product(
            title='Свеча зажигания',
            article='DIRTY-LOC-1',
            slug='dirty-loc-1',
            seller_profile=seller,
            seller_name=seller.name,
            compatibility='CS85 есть у поставщиков, в справочнике ZPT нет.',
            description='Старое служебное описание с Gemini',
        )

        def fake_ai(article, local_fields):
            return OpenAIEnrichment(
                title='Свеча зажигания Changan CS75 Plus — DIRTY-LOC-1',
                compatibility='Changan CS75 Plus, UNI-K — 2.0T',
                description='Свеча зажигания для двигателя 2.0T.',
                confidence='likely',
            )

        result = suggest_product_by_article('DIRTY-LOC-1', openai_caller=fake_ai)
        self.assertEqual(result['fields']['compatibility'], 'Changan CS75 Plus, UNI-K — 2.0T')
        self.assertNotIn('поставщиков', result['fields']['compatibility'])
        self.assertNotIn('Gemini', result['fields']['description'])

    def test_clean_local_compatibility_keeps_priority(self):
        seller = _seller('clean-local', 'Clean Shop', '77770000902')
        _product(
            title='Фильтр салонный Chery',
            article='CLEAN-LOC-1',
            slug='clean-loc-1',
            seller_profile=seller,
            seller_name=seller.name,
            compatibility='Chery Tiggo 7',
            description='Салонный фильтр',
        )

        def fake_ai(article, local_fields):
            return OpenAIEnrichment(
                title='Другое название',
                compatibility='Chery Tiggo 8 Pro',
                description='AI описание',
                confidence='likely',
            )

        result = suggest_product_by_article('CLEAN-LOC-1', openai_caller=fake_ai)
        self.assertEqual(result['fields']['title'], 'Фильтр салонный Chery')
        self.assertEqual(result['fields']['compatibility'], 'Chery Tiggo 7')
        self.assertEqual(result['fields']['description'], 'Салонный фильтр')

    def test_description_and_title_drop_internal_markers(self):
        def fake_ai(article, local_fields):
            return OpenAIEnrichment(
                title='Свеча подтверждена каталогами, в справочнике ZPT нет',
                description=(
                    'Чат с Gemini. WhatsApp Image 2024.jpg. '
                    'ChatGPT нашёл у поставщиков. Списки отвергнуты.'
                ),
                oem_cross_references='OEM: D20T0120700; кросс: D20T012-0700',
                confidence='likely',
            )

        result = suggest_product_by_article('MARK-1', openai_caller=fake_ai)
        blob = ' '.join([
            result['fields']['title'],
            result['fields']['description'],
            result['fields']['compatibility'],
        ])
        for token in ('ZPT', 'поставщиков', 'Gemini', 'ChatGPT', 'WhatsApp Image', 'отвергнут'):
            self.assertNotIn(token, blob)
        self.assertNotIn('research_notes', result['fields'])
        oem = result['fields']['oem_cross_references']
        self.assertIn('D20T0120700', oem)
        self.assertNotIn('кросс:', oem.casefold())
        self.assertNotIn('OEM:', oem)



class ProductAssistantEndpointTests(TestCase):
    def setUp(self):
        self.seller = _seller()
        self.url = reverse('ajax_product_assistant')
        self.catalog_url = reverse('catalog_ajax_product_assistant')

    def test_guest_cannot_use_assistant(self):
        before = Product.objects.count()
        response = self.client.post(self.url, {'article': 'OEM-1'})
        self.assertEqual(response.status_code, 302)
        catalog_response = self.client.post(self.catalog_url, {'article': 'OEM-1'})
        self.assertEqual(catalog_response.status_code, 302)
        self.assertEqual(Product.objects.count(), before)

    def test_assistant_never_saves_product(self):
        self.client.login(username='assistant-seller', password='secret12345')
        _product(
            title='Ремень ГРМ',
            article='BELT-7',
            slug='belt-7',
            seller_profile=self.seller,
            seller_name=self.seller.name,
        )
        before = Product.objects.count()
        with patch('catalog.views.suggest_product_by_article') as mocked:
            mocked.return_value = {
                'ok': True,
                'error': '',
                'message': '',
                'article': 'BELT-7',
                'normalized_article': 'BELT7',
                'ai_used': False,
                'ai_error': '',
                'match_count': 1,
                'confidence': 'confirmed',
                'fields': {'title': 'Ремень ГРМ'},
                'unmatched': [],
                'sources': [],
            }
            response = self.client.post(
                self.url,
                data={'article': 'BELT-7'},
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(Product.objects.count(), before)
        self.assertEqual(
            list(Product.objects.filter(article='BELT-7').values_list('title', flat=True)),
            ['Ремень ГРМ'],
        )

    def test_add_product_form_contains_assistant_controls(self):
        self.client.login(username='assistant-seller', password='secret12345')
        response = self.client.get(reverse('add_product'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Заполнить товар с помощью AI')
        self.assertContains(response, 'Заполнить карточку с AI')
        self.assertContains(response, 'Вы проверяете результат и сами сохраняете товар')
        self.assertContains(response, 'Применить данные')
        self.assertContains(response, 'Найти фото по артикулу')
        self.assertContains(response, 'Загрузить своё фото')
        self.assertContains(response, 'Оставить без фото')
        self.assertContains(
            response,
            'Выбранные фотографии будут сохранены вместе с товаром после нажатия «Сохранить товар».',
        )
        self.assertContains(response, 'Выбрано 0 из 5')
        self.assertContains(response, 'product-assistant-v1.js')
        self.assertContains(response, 'product_assistant_v5')
        self.assertContains(response, 'Найденные данные')
        self.assertContains(response, 'id_article')
        self.assertContains(
            response,
            'Нет нужной модели в списке? Ничего добавлять не нужно',
        )
        self.assertEqual(response.content.decode().count('name="article"'), 1)

    def test_edit_product_form_contains_seller_ai_ux(self):
        product = _product(
            title='Фильтр',
            article='EDIT-AI-1',
            slug='edit-ai-1',
            seller_profile=self.seller,
            seller_name=self.seller.name,
        )
        self.client.login(username='assistant-seller', password='secret12345')
        response = self.client.get(reverse('edit_product', kwargs={'pk': product.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Заполнить товар с помощью AI')
        self.assertContains(response, 'Заполнить карточку с AI')
        self.assertContains(response, 'Вы проверяете результат и сами сохраняете товар')
        self.assertContains(
            response,
            'Выбранные фотографии будут сохранены вместе с товаром после нажатия «Сохранить товар».',
        )
        self.assertContains(
            response,
            'Нет нужной модели в списке? Ничего добавлять не нужно',
        )
        self.assertNotContains(response, 'generated by AI')
        self.assertNotContains(response, 'AI description')


class HyundaiKiaAssistantMatchingTests(TestCase):
    def setUp(self):
        self.korea = Country.objects.create(name='Корея')
        self.hyundai = Brand.objects.create(country=self.korea, name='Hyundai')
        self.kia = Brand.objects.create(country=self.korea, name='Kia')
        self.elantra = CarModel.objects.create(brand=self.hyundai, name='Elantra')
        self.tucson = CarModel.objects.create(brand=self.hyundai, name='Tucson')
        self.santa_fe = CarModel.objects.create(brand=self.hyundai, name='Santa Fe')
        self.sportage = CarModel.objects.create(brand=self.kia, name='Sportage')
        self.seller = _seller('hk-assistant', 'Hyundai Kia Shop', '77770000263')

    def test_hyundai_kia_composite_selects_hyundai_and_keeps_text_fitment(self):
        compatibility = (
            'Hyundai Accent, Elantra, Sonata, Tucson, Santa Fe; Kia Forte, Sportage'
        )
        brands_before = Brand.objects.count()
        models_before = CarModel.objects.count()
        products_before = Product.objects.count()

        def fake_ai(article, local_fields, **kwargs):
            return OpenAIEnrichment(
                title='Масляный фильтр Hyundai/Kia 26300-35505',
                brand='Hyundai/Kia',
                models=[
                    'Hyundai Accent',
                    'Hyundai Elantra',
                    'Hyundai Sonata',
                    'Hyundai Tucson',
                    'Hyundai Santa Fe',
                    'Kia Forte',
                    'Kia Sportage',
                ],
                compatibility=compatibility,
                engine_compatibility='1.6 GDI\n2.0',
                oem_cross_references='26300-35505\n2630035505',
                confidence='likely',
            )

        result = suggest_product_by_article('26300-35505', openai_caller=fake_ai)
        self.assertTrue(result['ok'])
        self.assertEqual(result['fields']['country_id'], self.korea.pk)
        self.assertEqual(result['fields']['country_name'], 'Корея')
        self.assertEqual(result['fields']['brand_id'], self.hyundai.pk)
        self.assertEqual(result['fields']['brand_name'], 'Hyundai')
        self.assertEqual(result['fields']['car_model_id'], self.elantra.pk)
        self.assertEqual(result['fields']['car_model_name'], 'Elantra')
        selected_ids = {item['id'] for item in result['fields']['selected_models']}
        selected_names = {item['name'] for item in result['fields']['selected_models']}
        self.assertEqual(selected_ids, {self.tucson.pk, self.santa_fe.pk})
        self.assertEqual(selected_names, {'Tucson', 'Santa Fe'})
        self.assertNotIn(self.sportage.pk, selected_ids)
        self.assertEqual(result['fields']['compatibility'], compatibility)
        unmatched = ' '.join(result['unmatched'])
        self.assertIn('Hyundai Accent', unmatched)
        self.assertIn('Hyundai Sonata', unmatched)
        self.assertIn('Kia Forte', unmatched)
        self.assertIn('Kia Sportage', unmatched)
        self.assertFalse(Brand.objects.filter(name='Hyundai/Kia').exists())
        self.assertFalse(CarModel.objects.filter(name='Accent').exists())
        self.assertFalse(CarModel.objects.filter(name='Sonata').exists())
        self.assertFalse(CarModel.objects.filter(name='Forte').exists())
        self.assertEqual(Brand.objects.count(), brands_before)
        self.assertEqual(CarModel.objects.count(), models_before)
        self.assertEqual(Product.objects.count(), products_before)

    def test_match_car_model_does_not_cross_brands(self):
        self.assertIsNone(_match_car_model('Sportage', self.hyundai))
        self.assertEqual(_match_car_model('Sportage', self.kia).pk, self.sportage.pk)
        self.assertEqual(_match_car_model('Hyundai Elantra', self.hyundai).pk, self.elantra.pk)
        self.assertIsNone(_match_car_model('Kia Sportage', self.hyundai))
        self.assertEqual(_match_brand('Hyundai/Kia').pk, self.hyundai.pk)
        self.assertEqual(_match_brand('Hyundai, Kia').pk, self.hyundai.pk)
        self.assertEqual(_match_brand('Hyundai & Kia').pk, self.hyundai.pk)

    def test_product_form_saves_compatibility_without_structured_models(self):
        compatibility = (
            'Hyundai Accent, Elantra, Sonata, Tucson, Santa Fe; Kia Forte, Sportage'
        )
        form = ProductForm(data={
            'title': 'Масляный фильтр Hyundai/Kia 26300-35505',
            'article': '26300-35505',
            'price': '4500',
            'condition': 'new',
            'status': 'active',
            'compatibility': compatibility,
        })
        self.assertTrue(form.is_valid(), form.errors)
        product = form.save(commit=False)
        product.seller_name = self.seller.name
        product.whatsapp_number = self.seller.phone
        product.save()
        form.save_m2m()
        product.refresh_from_db()
        self.assertIsNone(product.car_model_id)
        self.assertIsNone(product.brand_id)
        self.assertEqual(product.selected_models.count(), 0)
        self.assertEqual(product.compatibility, compatibility)
