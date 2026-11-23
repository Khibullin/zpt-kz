import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import CatalogImportBatch, Product, ProductKaspiListing, SellerProfile
from repricer.models import KaspiActiveExportRow, KaspiRepricerSearchSettings


class RepricerApifyRulesTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="rules-staff", is_staff=True)
        self.other = User.objects.create_user(username="rules-other")
        seller = SellerProfile.objects.create(
            user=self.staff, name="AG Parts", phone="+77000000000"
        )
        now = timezone.now()
        self.batch = CatalogImportBatch.objects.create(
            seller_profile=seller,
            source=CatalogImportBatch.SOURCE_KASPI_LISTING_FACTS,
            filename="ACTIVE.xlsx",
            file_sha256="a" * 64,
            started_at=now - timedelta(seconds=1),
            finished_at=now,
            mode=CatalogImportBatch.MODE_WRITE,
            source_scope=CatalogImportBatch.SCOPE_FULL,
            status=CatalogImportBatch.STATUS_SUCCESS,
            source_row_count=2,
        )
        product = Product.objects.create(
            title="Фильтр масляный 1017110XEN01", article="1017110XEN01",
            seller_profile=seller, seller_name="AG Parts", whatsapp_number="+77000000000",
        )
        listing = ProductKaspiListing.objects.create(
            product=product, master_sku="1017110XEN01",
            merchant_sku="1017110XEN01",
            public_url="https://kaspi.kz/shop/p/filtr-141407587/",
            public_url_verified_at=now,
        )
        KaspiActiveExportRow.objects.create(
            batch=self.batch, row_number=2, listing=listing,
            master_sku="1017110XEN01", product_name="Фильтр масляный 1017110XEN01",
            price=2300, quantity=5, status="UPDATED",
        )
        KaspiActiveExportRow.objects.create(
            batch=self.batch, row_number=3, master_sku="UNMAPPED",
            product_name="Фильтр салонный BYD Song Plus",
            brand="ag-parts", price=2800, quantity=2,
            status="MISSING_LISTING",
        )

    def test_rules_are_staff_only(self):
        self.client.force_login(self.other)
        self.assertNotEqual(self.client.get(reverse("repricer:rules")).status_code, 200)
        self.assertNotEqual(self.client.get(reverse("repricer:apify_export")).status_code, 200)

    def test_rules_saved_and_used_in_actor_json(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("repricer:rules")), "Правила мониторинга")
        response = self.client.post(reverse("repricer:rules"), {
            "cities": ["Almaty", "Astana", "Atyrau"],
            "cards_per_product": "3",
        })
        self.assertRedirects(response, reverse("repricer:rules"))
        settings = KaspiRepricerSearchSettings.objects.get(pk=1)
        self.assertEqual(settings.cities, ["Almaty", "Astana", "Atyrau"])
        self.assertEqual(settings.cards_per_product, 3)

        response = self.client.get(reverse("repricer:apify_export"))
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["cities"], ["Almaty", "Astana", "Atyrau"])
        self.assertEqual(data["maxItemsPerQuery"], 3)
        self.assertEqual(data["maxOffersPerProduct"], 0)
        self.assertTrue(data["includeOffers"])
        self.assertFalse(data["includeReviews"])
        self.assertEqual(len(data["startUrls"]), 1)
        self.assertEqual(len(data["searchQueries"]), 1)
        self.assertNotIn("mapping", data)
        self.assertNotIn("our_price", data)

        mapping = json.loads(self.client.get(
            reverse("repricer:apify_export") + "?format=mapping"
        ).content)
        self.assertEqual(len(mapping["rows"]), 2)
        self.assertEqual(mapping["rows"][1]["sku"], "UNMAPPED")
        self.assertTrue(mapping["rows"][1]["requires_result_verification"])

    def test_invalid_rules_do_not_change_settings(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("repricer:rules"), {
            "cities": ["FakeCity"], "cards_per_product": "9",
        })
        self.assertEqual(KaspiRepricerSearchSettings.objects.get(pk=1).cards_per_product, 2)

    def test_later_completed_batch_replaces_source_not_merges(self):
        self.client.force_login(self.staff)
        newer = CatalogImportBatch.objects.create(
            seller_profile=self.batch.seller_profile,
            source=CatalogImportBatch.SOURCE_KASPI_LISTING_FACTS,
            filename="ACTIVE_new.xlsx", file_sha256="b" * 64,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            mode=CatalogImportBatch.MODE_WRITE,
            source_scope=CatalogImportBatch.SCOPE_FULL,
            status=CatalogImportBatch.STATUS_SUCCESS,
            source_row_count=1,
        )
        KaspiActiveExportRow.objects.create(
            batch=newer, row_number=2, master_sku="NEW-111",
            product_name="Свеча NEW-111", price=1700,
            status="MISSING_LISTING",
        )
        mapping = json.loads(self.client.get(
            reverse("repricer:apify_export") + "?format=mapping"
        ).content)
        self.assertEqual(mapping["source_filename"], "ACTIVE_new.xlsx")
        self.assertEqual(len(mapping["rows"]), 1)
        self.assertEqual(mapping["rows"][0]["sku"], "NEW-111")
