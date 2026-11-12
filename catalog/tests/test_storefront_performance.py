from unittest.mock import patch
from django.test import TestCase
from django.db import connection
from django.test.utils import CaptureQueriesContext
from catalog.tests.test_storefront import StorefrontTests
from catalog.storefront import apply_filters


class StorefrontPerformanceTests(TestCase):
    setUpTestData = classmethod(StorefrontTests.setUpTestData.__func__)
    setUp = StorefrontTests.setUp
    def test_country_reuses_one_vehicle_scope_and_root_snapshot(self):
        with patch('catalog.storefront.apply_filters', wraps=apply_filters) as matcher:
            response = self.client.get('/', {'country': self.country.pk, 'picker': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['result_count'], 1)
        self.assertEqual(matcher.call_count, 1)
        self.assertEqual(response.context['facets']['brand'][0]['label'], 'Chery')

    def test_home_count_and_page_ids_do_not_join_supplier_tables(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        counts = [q['sql'] for q in queries if 'COUNT(' in q['sql'] and 'catalog_product' in q['sql']]
        self.assertTrue(counts)
        self.assertTrue(all('procurement_offer' not in sql for sql in counts))

    def test_repeated_home_reuses_order_but_reads_live_price_and_stock(self):
        from catalog.models import Product
        self.client.get('/')
        Product.objects.filter(pk=self.product.pk).update(price=8900, stock_qty=0)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/')
        self.assertEqual(response.context['products'][0].price, 8900)
        self.assertFalse(response.context['products'][0].public_stock['can_buy'])
        self.assertFalse(any('COUNT(' in q['sql'] and 'catalog_product' in q['sql'] for q in queries))
