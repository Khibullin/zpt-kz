from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .kaspi_search_queries import monitoring_search_queries
from .omega_selection_requests import request_queries


class MonitoringQueryPolicyTests(SimpleTestCase):
    def test_search_omits_brand_and_preserves_complete_article(self):
        queries = monitoring_search_queries('CTR', 'CQ0735R', 'CTR рычаг подвески CQ0735R')
        self.assertEqual(queries, ['рычаг подвески CQ0735R', 'CQ0735R'])
        self.assertNotIn('CQ0735L', queries)
        self.assertEqual(monitoring_search_queries('WXQP', '0051331A'), ['0051331A'])

    def test_explicit_manufacturer_alias_is_removed_only_for_search(self):
        self.assertEqual(monitoring_search_queries('PARTS-MALL', 'PSC-B003',
            'PMC PARTS MALL ролик PSC-B003'), ['ролик PSC-B003', 'PSC-B003'])
        self.assertEqual(monitoring_search_queries('CTR', '', 'рычаг'), [])

    def test_saved_versions_are_reproducible(self):
        row = dict(brand='CTR', article='CQ0735R', name='CTR рычаг CQ0735R')
        saved = SimpleNamespace(details={'supplier_name': 'Phaeton DC'})
        with patch('procurement.omega_selection_requests.request_rows', return_value=[row]):
            self.assertEqual([r['query'] for r in request_queries(saved)], ['CTR CQ0735R'])
            saved.details['search_policy'] = 'phaeton_article_queries_v2'
            self.assertEqual([r['query'] for r in request_queries(saved)], ['CTR CQ0735R', 'CQ0735R'])
            saved.details['search_policy'] = 'article_only_v3'
            self.assertEqual([r['query'] for r in request_queries(saved)], ['CQ0735R'])
            saved.details['search_policy'] = 'name_article_v4'
            self.assertEqual([r['query'] for r in request_queries(saved)], ['рычаг CQ0735R', 'CQ0735R'])

    def test_repricer_uses_same_policy_without_dropping_suffix(self):
        from repricer.apify_json import _queries_for_row
        row = SimpleNamespace(listing=None, product_name='CTR рычаг CQ0735R',
            brand='CTR', merchant_sku='CQ0735R', master_sku='123456789')
        queries, _, brand, article = _queries_for_row(row)
        self.assertEqual(queries, ['рычаг CQ0735R', 'CQ0735R'])
        self.assertEqual((brand, article), ('CTR', 'CQ0735R'))
