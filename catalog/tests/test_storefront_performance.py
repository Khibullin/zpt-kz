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

    def test_country_snapshot_avoids_matching_and_keeps_prices_stock_live(self):
        from catalog.storefront import refresh_country_facets
        from catalog.models import Product
        self.assertTrue(refresh_country_facets())
        Product.objects.filter(pk=self.product.pk).update(price=4500,stock_qty=0)
        with patch('catalog.storefront.apply_filters',side_effect=AssertionError('No matching in country navigation')), patch('catalog.storefront.photo_ordered_products',side_effect=AssertionError('No global sorting')):
            response=self.client.get('/',{'country':self.country.pk})
        self.assertEqual(response.context['result_count'],1)
        self.assertEqual(response.context['products'][0].price,4500)
        self.assertFalse(response.context['products'][0].public_stock['can_buy'])
        # Availability and other combinations must still use live filters.
        self.assertEqual(self.client.get('/',{'country':self.country.pk,'stock':'in'}).context['result_count'],0)

    def test_brand_and_model_reuse_prepared_country_parent_counts(self):
        from catalog.storefront import refresh_country_facets, facet_values
        self.assertTrue(refresh_country_facets())
        for selection in ({'country':self.country.pk,'brand':self.brand.pk},
                          {'country':self.country.pk,'brand':self.brand.pk,'model':self.model.pk}):
            with patch('catalog.storefront.facet_values',wraps=facet_values) as aggregate:
                response=self.client.get('/',selection)
            self.assertEqual(response.context['result_count'],1)
            self.assertEqual(response.context['facets']['brand'][0]['count'],1)
            self.assertNotIn('brand',[call.args[0] for call in aggregate.call_args_list])

    def test_legacy_cross_country_brand_relationships_remain_supported(self):
        from catalog.models import Country, Brand
        other=Brand.objects.create(name='Other',country=Country.objects.create(name='Other country'))
        self.product.selected_brands.add(other)
        response=self.client.get('/',{'country':self.country.pk,'brand':other.pk})
        self.assertEqual(response.context['result_count'],1)
        response=self.client.get('/',{'country':self.country.pk,'brand':other.pk,'model':self.model.pk})
        self.assertEqual(response.context['result_count'],0)
