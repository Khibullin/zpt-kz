import importlib
import json
from datetime import timedelta
from unittest.mock import patch
from django.apps import apps
from django.db import connection
from django.test import TestCase, override_settings
from types import SimpleNamespace
from django.utils import timezone
from catalog.agparts_verified_offers import AUDITED_OFFERS, approved_offer_entry
from catalog.tests.test_agparts_api_v1 import CatalogApiTests, LIST_URL
from catalog.models import ProductKaspiListing, ProductWarehouseStock
from repricer.models import KaspiCompetitorOfferSnapshot


class ReviewedOffersTests(CatalogApiTests):
    @override_settings(AGPARTS_KASPI_MERCHANT_ID="30363568")
    def test_review_import_is_scoped_idempotent_and_keeps_product_and_stock(self):
        row = {"product_id":self.product.pk, "master_sku":"reviewed-listing", "pack_qty":1,
               "public_url":"https://kaspi.kz/shop/p/test-123/?c=750000000&m=30363568&ms=true",
               "price":2800,"checked_at":timezone.now().isoformat()}
        records = {self.product.article:row}
        migration = importlib.import_module("catalog.migrations.0074_agparts_verified_kaspi_offers_20261021")
        prices_before = self.product.price
        stock_before = list(ProductWarehouseStock.objects.values_list("pk","quantity"))
        with patch.object(migration.Path, "read_text", return_value=json.dumps({"offers":records})):
            editor = SimpleNamespace(connection=connection)
            migration.import_review(apps, editor)
            migration.import_review(apps, editor)
        listing = ProductKaspiListing.objects.get(product=self.product,master_sku="reviewed-listing")
        self.assertFalse(listing.publish_to_kaspi)
        self.assertEqual(KaspiCompetitorOfferSnapshot.objects.filter(listing=listing).count(),1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, prices_before)
        self.assertEqual(list(ProductWarehouseStock.objects.values_list("pk","quantity")),stock_before)
        with patch.dict(AUDITED_OFFERS,records):
            result=self.call(LIST_URL+'?details=1').json()['products'][0]
        self.assertEqual(result['kaspi_pack_qty'],1)
        self.assertEqual(result['retail_price_kzt'],2800)
        # A newer unavailable observation must suppress the manual observed price.
        KaspiCompetitorOfferSnapshot.objects.create(listing=listing,seller_code='30363568',seller_name='AG Parts',price=2800,is_available=False,source='office_collector',captured_at=timezone.now())
        with patch.dict(AUDITED_OFFERS,records):
            self.assertIsNone(self.call().json()['products'][0]['retail_price_kzt'])

    def test_identity_and_explicit_configuration_win_over_review(self):
        entry={'kaspi_pack_qty':None,'kaspi_master_sku':None}
        with patch.dict(AUDITED_OFFERS,{self.product.article:{'product_id':999,'master_sku':'x','pack_qty':1}}):
            self.assertIs(approved_offer_entry(self.product,entry),entry)
        explicit={'kaspi_pack_qty':4,'kaspi_master_sku':'owner'}
        with patch.dict(AUDITED_OFFERS,{self.product.article:{'product_id':self.product.pk,'master_sku':'x','pack_qty':1}}):
            self.assertIs(approved_offer_entry(self.product,explicit),explicit)

    def test_frozen_review_preserves_four_pack_and_own_merchant(self):
        from urllib.parse import urlsplit,parse_qs
        self.assertEqual(len(AUDITED_OFFERS),56)
        self.assertNotIn('F188107041',AUDITED_OFFERS)
        self.assertEqual(AUDITED_OFFERS['F4J163707010']['pack_qty'],4)
        for row in AUDITED_OFFERS.values():
            parsed=urlsplit(row['public_url']);query=parse_qs(parsed.query)
            self.assertEqual(parsed.hostname,'kaspi.kz')
            self.assertEqual(query['m'],['30363568'])
            self.assertEqual(query['ms'],['true'])
