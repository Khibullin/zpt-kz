"""Safety regression coverage for the Omega Apify export generator."""
import json
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Membership, Offer, Supplier, WholesaleSelectionRule
from .selection_exports import APIFY_CITIES, JSON_BATCH_SIZE, apify_queries, article_variants


class OmegaApifyExportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('omega-apify-export-admin')
        Membership.objects.create(user=cls.user, role='admin')
        cls.omega = Supplier.objects.create(name='Omega')
        cls.rule = WholesaleSelectionRule.objects.create(
            singleton_key=1, supplier=cls.omega, min_cost=Decimal('2000'),
            max_vehicle_age=20, classification_mode='supplier',
            category_groups=[], in_stock_only=True)
        years = str(date.today().year)
        cls.first = Offer.objects.create(
            supplier=cls.omega, brand='SRR', article='085066',
            name='Рулевой наконечник', category='Рулевой наконечник',
            cost=Decimal('2000'), stock='5', applicability=years)
        cls.second = Offer.objects.create(
            supplier=cls.omega, brand='TEST', article='100200',
            name='Колодки', category='Колодки',
            cost=Decimal('3300'), stock='2', applicability=years)
        Offer.objects.create(
            supplier=cls.omega, brand='TEST', article='BAD-STOCK',
            name='Нет остатка', cost=Decimal('5000'), stock='0',
            applicability=years)

    def setUp(self):
        self.client.force_login(self.user)

    def test_variants_do_not_remove_internal_or_trailing_zeros(self):
        self.assertEqual(article_variants('SRR 085066'), ['SRR 085066', 'SRR 85066'])
        self.assertEqual(article_variants('085066'), ['085066', '85066'])
        self.assertEqual(article_variants('AB00100'), ['AB00100'])
        self.assertEqual(article_variants('100200'), ['100200'])

    def test_search_batch_size_and_complete_cities(self):
        self.assertEqual(JSON_BATCH_SIZE, 1000)
        self.assertEqual(len(APIFY_CITIES), 10)
        self.assertIn('Kostanay', APIFY_CITIES)
        response = self.client.get(reverse('procurement:selection_apify_json'))
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['cities'], list(APIFY_CITIES))
        self.assertEqual(data['searchQueries'], ['Рулевой наконечник 085066', '085066', 'Рулевой наконечник 85066', '85066', 'Колодки 100200', '100200'])
        self.assertEqual(data['maxItemsPerQuery'], 20)
        self.assertEqual(data['maxItems'], 0)
        self.assertEqual(data['sortOrder'], 'price-asc')
        self.assertTrue(data['includeOffers'])
        self.assertEqual(data['startUrls'], [])
        self.assertEqual(self.first.cost, Decimal('2000'))

    def test_selected_supplier_price_threshold_is_respected_on_export(self):
        Offer.objects.create(
            supplier=self.omega, brand='CHEAP', article='LOW-001',
            name='Рычаг LOW-001', category='Рычаг',
            cost=Decimal('1999.99'), stock='10',
            applicability=str(date.today().year))
        self.rule.min_cost = Decimal('1000')
        self.rule.save(update_fields=['min_cost'])
        response = self.client.get(reverse('procurement:selection_apify_json'))
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIn('LOW-001', data['searchQueries'])
        self.rule.min_cost = Decimal('2000')
        self.rule.save(update_fields=['min_cost'])
        higher = self.client.get(reverse('procurement:selection_apify_json'))
        self.assertNotIn('LOW-001', json.loads(higher.content)['searchQueries'])
        self.assertIn('085066', data['searchQueries'])
        self.assertEqual(data['sortOrder'], 'price-asc')
        self.assertEqual(data['maxItemsPerQuery'], 20)

    def test_configurable_cities_and_cards(self):
        response = self.client.get(reverse('procurement:selection_apify_json'),
                                   {'city': ['Almaty', 'Astana'], 'cards': '5'})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['cities'], ['Almaty', 'Astana'])
        self.assertEqual(data['maxItemsPerQuery'], 5)

    def test_invalid_cards_rejected(self):
        response = self.client.get(reverse('procurement:selection_apify_json'), {'cards': '21'})
        self.assertEqual(response.status_code, 400)

    def test_traceability_and_no_data_modifications(self):
        url = reverse('procurement:selection_apify_json')
        before = list(Offer.objects.order_by('pk').values_list('pk', 'cost', 'stock'))
        response = self.client.get(url, {'format': 'mapping'})
        self.assertEqual(response.status_code, 200)
        rows = json.loads(response.content)['mapping']
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]['offer_id'], self.first.pk)
        self.assertEqual(rows[1]['original_article'], '085066')
        self.assertEqual(before, list(Offer.objects.order_by('pk').values_list('pk', 'cost', 'stock')))
        self.assertEqual(self.client.get(url, {'batch': 2}).status_code, 404)

    def test_de_dupe_queries_case_insensitive(self):
        Offer.objects.create(
            supplier=self.omega, brand='srr', article='85066', name='Дубль поиска',
            category='Рулевой наконечник', cost=Decimal('2001'), stock='3',
            applicability=str(date.today().year))
        queries = [row['query'].casefold() for row in apify_queries(self.rule)]
        self.assertEqual(queries.count('85066'), 1)
        self.assertEqual(len(queries), len(set(queries)))

    def test_known_urls_exported_only_for_batch_skus(self):
        self.first.kaspi_url = 'https://kaspi.kz/shop/p/part-123456789/?c=750000000'
        self.first.save()
        self.second.kaspi_url = 'https://example.com/shop/p/part-987654321/'
        self.second.save()
        response = self.client.get(reverse('procurement:selection_apify_json'))
        data = json.loads(response.content)
        self.assertEqual(data['startUrls'], ['https://kaspi.kz/shop/p/part-123456789/'])
        self.assertEqual(data['searchQueries'], ['Рулевой наконечник 085066', '085066', 'Рулевой наконечник 85066', '85066', 'Колодки 100200', '100200'])
