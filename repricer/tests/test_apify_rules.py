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
        self.assertGreaterEqual(len(data["searchQueries"]), 2)
        self.assertIn("1017110XEN01", data["searchQueries"])
        self.assertIn("https://kaspi.kz/shop/p/filtr-141407587/", data["startUrls"])
        self.assertNotIn("mapping", data)
        self.assertNotIn("our_price", data)

        mapping = json.loads(self.client.get(
            reverse("repricer:apify_export") + "?format=mapping"
        ).content)
        self.assertEqual(len(mapping["rows"]), 2)
        self.assertEqual(mapping["rows"][1]["sku"], "UNMAPPED")
        self.assertTrue(mapping["rows"][1]["requires_result_verification"])


    def test_vague_name_is_qualified_by_source_sku(self):
        from repricer.apify_json import input_and_mapping
        row = KaspiActiveExportRow(batch=self.batch, row_number=81,
            master_sku="365030770", product_name="Воздушный фильтр",
            price=2730, status="MISSING_LISTING")
        payload, mapping = input_and_mapping([row], ["Almaty"], 2)
        self.assertEqual(payload["searchQueries"], ["Воздушный фильтр"])
        self.assertTrue(mapping[0]["source_sku_only_fallback"])
        self.assertTrue(mapping[0]["requires_result_verification"])

    def test_alphanumeric_sku_is_included(self):
        from repricer.apify_json import input_and_mapping
        row = KaspiActiveExportRow(batch=self.batch, row_number=86,
            master_sku="F081109111HD", product_name="AS воздушный фильтр Jetour DASHING",
            brand="ag-parts", price=2730, status="MISSING_LISTING")
        payload, mapping = input_and_mapping([row], ["Almaty"], 2)
        self.assertIn("F081109111HD", payload["searchQueries"][0])
        self.assertTrue(mapping[0]["requires_result_verification"])

    def test_one_click_generation_saves_checked_cities(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("repricer:rules"), {
            "cities": ["Almaty", "Astana", "Shymkent"],
            "cards_per_product": "2",
            "return_to": "dashboard",
            "action": "generate",
        })
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["cities"], ["Almaty", "Astana", "Shymkent"])
        saved = KaspiRepricerSearchSettings.objects.get(pk=1)
        self.assertEqual(saved.cities, payload["cities"])
        self.assertContains(self.client.get(reverse("repricer:dashboard")),
                            'form="repricer-rules-form"')

    def test_one_click_requires_one_city(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("repricer:rules"), {
            "cards_per_product": "2", "return_to": "dashboard",
            "action": "generate",
        })
        self.assertRedirects(response, reverse("repricer:dashboard"))
        self.assertEqual(len(KaspiRepricerSearchSettings.objects.get(pk=1).cities), 6)

    def test_full_mode_searches_verified_product_by_url_and_article(self):
        from repricer.apify_json import input_and_mapping
        rows = list(KaspiActiveExportRow.objects.filter(batch=self.batch).select_related(
            "listing", "listing__product", "listing__product__brand"))
        payload, mapping = input_and_mapping(rows, ["Almaty", "Astana"], 3)
        self.assertEqual(len(payload["startUrls"]), 1)
        self.assertIn("1017110XEN01", payload["searchQueries"])
        self.assertTrue(mapping[0]["requires_result_verification"])

    def test_additional_mode_keeps_verified_url_out_of_recheck(self):
        from repricer.apify_json import remaining_input_and_mapping
        rows = list(KaspiActiveExportRow.objects.filter(batch=self.batch).select_related(
            "listing", "listing__product", "listing__product__brand"))
        payload, mapping = remaining_input_and_mapping(rows, ["Almaty"], 3)
        self.assertEqual(payload["startUrls"], [])
        self.assertEqual(len(mapping), 2)
        self.assertIn("1017110XEN01", payload["searchQueries"])

    def test_followup_queries_only_missing_competitor_prices(self):
        from repricer.apify_json import remaining_input_and_mapping
        rows = list(KaspiActiveExportRow.objects.filter(batch=self.batch).select_related(
            "listing", "listing__product", "listing__product__brand"))
        # Current live listing had a verified competing price in a previous run.
        rows[0].competitor_min_price = 2000
        rows[1].product_name = "FORGA стойка стабилизатора F1207022"
        rows[1].brand = "FORGA"
        payload, mapping = remaining_input_and_mapping(rows, ["Almaty"], 3)
        self.assertEqual(payload["startUrls"], [])
        self.assertEqual(payload["cities"], ["Almaty"])
        self.assertEqual(payload["maxItemsPerQuery"], 3)
        self.assertEqual(len(mapping), 1)
        self.assertIn("F1207022", " ".join(payload["searchQueries"]))
        self.assertTrue(mapping[0]["requires_result_verification"])
        self.assertNotIn("1017110XEN01", " ".join(payload["searchQueries"]))

    def test_followup_download_uses_last_saved_cities(self):
        from repricer.apify_json import remaining_input_and_mapping
        self.client.force_login(self.staff)
        response = self.client.post(reverse("repricer:rules"), {
            "cities": ["Almaty", "Shymkent"], "cards_per_product": "3",
            "return_to": "dashboard", "action": "generate_remaining",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("remaining", response["Content-Disposition"])
        payload = json.loads(response.content)
        self.assertEqual(payload["cities"], ["Almaty", "Shymkent"])
        self.assertEqual(payload["maxItemsPerQuery"], 3)
        self.assertEqual(payload["startUrls"], [])

    def test_invalid_rules_do_not_change_settings(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("repricer:rules"), {
            "cities": ["FakeCity"], "cards_per_product": "9",
        })
        self.assertEqual(KaspiRepricerSearchSettings.objects.get(pk=1).cards_per_product, 3)

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
