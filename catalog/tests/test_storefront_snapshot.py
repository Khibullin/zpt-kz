from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.core.cache import caches
from django.utils import timezone
from django.contrib.auth.models import User
from catalog.models import Product, SellerProfile, StorefrontFacetSnapshot, Category
from catalog.storefront import refresh_home_facets

class StorefrontSnapshotTests(TestCase):
    def setUp(self):
        self.seller=SellerProfile.objects.create(user=User.objects.create(username='snapshot'),name='AG Parts',slug='ag-parts')
        self.p=Product.objects.create(title='Фильтр тест',seller_profile=self.seller,price=1300,stock_qty=3,category=Category.objects.create(name='Фильтры snapshot'))
        for alias in ('storefront_common','storefront_facets'):
            caches[alias].clear()

    def test_first_request_without_snapshot_never_runs_aggregations(self):
        with patch('catalog.storefront.build_home_facets',side_effect=AssertionError('No slow work in request')),patch('catalog.storefront.facet_values',side_effect=AssertionError('No aggregation')):
            response=self.client.get('/')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['result_count'],1)
        self.assertTrue(response.context['filters_pending'])
        self.assertIsNone(response.context['facets']['category'][0]['count'])

    def test_snapshot_survives_memory_cache_loss_and_keeps_prices_live(self):
        self.assertTrue(refresh_home_facets())
        self.assertFalse(refresh_home_facets())
        StorefrontFacetSnapshot.objects.update(built_at=timezone.now()-timedelta(days=1))
        for alias in ('storefront_common','storefront_facets'):caches[alias].clear()
        Product.objects.filter(pk=self.p.pk).update(price=2600,stock_qty=0)
        with patch('catalog.storefront.facet_values',side_effect=AssertionError('Must use durable result')):
            response=self.client.get('/')
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.context['filters_pending'])
        self.assertEqual(response.context['facets']['category'][0]['count'],1)
        self.assertEqual(response.context['products'][0].price,2600)
        self.assertFalse(response.context['products'][0].public_stock['can_buy'])

    def test_refresh_failure_preserves_previous_complete_snapshot(self):
        refresh_home_facets()
        before=StorefrontFacetSnapshot.objects.get(pk='home').payload
        with patch('catalog.storefront.build_home_facets',side_effect=RuntimeError('Source unavailable')):
            with self.assertRaises(RuntimeError):refresh_home_facets(force=True)
        self.assertEqual(StorefrontFacetSnapshot.objects.get(pk='home').payload,before)
