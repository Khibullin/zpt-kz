from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.article_utils import normalize_article
from catalog.models import Brand, CarModel, Category, Country, Product, SellerProfile
from catalog.product_assistant import OpenAIEnrichment, suggest_product_by_article


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
        self.assertContains(response, 'Подобрать данные по артикулу')
        self.assertContains(response, 'Применить данные')
        self.assertContains(response, 'Найти фото по артикулу')
        self.assertContains(response, 'Загрузить своё фото')
        self.assertContains(response, 'Оставить без фото')
        self.assertContains(response, 'product-assistant-v1.js')
