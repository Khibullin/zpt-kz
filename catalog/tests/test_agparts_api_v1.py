"""AG Parts internal v1 API regression tests (isolated test DB only).

Never reach production, supplier APIs, Kaspi, or paid services.
"""
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from catalog.agparts_api_manifest import ManifestError, parse_agparts_manifest
from repricer.models import KaspiCompetitorOfferSnapshot
from catalog.models import (
    Brand, CarModel, Country, Product, ProductFitment, ProductKaspiListing,
    ProductWarehouseStock, SellerProfile, Warehouse,
)


TEST_TOKEN = "test-AG-parts-" + "x" * 44
LIST_URL = "/internal/agparts/v1/products/"
VEHICLES_URL = "/internal/agparts/v1/vehicles/"


class ManifestValidationTests(TestCase):
    def test_manifest_denies_invalid_brand_category_and_translation(self):
        doc = {
            "version": 1,
            "products": {
                "001234": {
                    "official_brand_verified": True,
                    "category": "cabin",
                    "title": {"ru": "Тест", "kk": "Сынақ", "zh": "测试"},
                    "supplier_confirmed_at": None,
                    "lead_time_days": None,
                    "kaspi_pack_qty": None,
                    "kaspi_master_sku": None,
                },
            },
        }
        self.assertIn("001234", parse_agparts_manifest(json.dumps(doc)))
        for key, value in (
            ("official_brand_verified", False),
            ("category", "engine_oil"),
            ("title", {"ru": "Тест"}),
            ("lead_time_days", 3),
        ):
            with self.subTest(field=key):
                altered = json.loads(json.dumps(doc))
                altered["products"]["001234"][key] = value
                with self.assertRaises(ManifestError):
                    parse_agparts_manifest(json.dumps(altered))

    def test_f4j_spark_is_four_pack_only(self):
        base = {
            "version": 1,
            "products": {
                "F4J163707010": {
                    "official_brand_verified": True,
                    "category": "spark",
                    "title": {
                        "ru": "Свечи AG Parts, комплект 4 шт.",
                        "kk": "AG Parts оталдыру шамдары, 4 дана",
                        "zh": "AG Parts 4支火花塞套装",
                    },
                    "supplier_confirmed_at": None,
                    "lead_time_days": None,
                    "kaspi_pack_qty": 4,
                    "kaspi_master_sku": "EXAMPLE-FOUR-PACK",
                },
            },
        }
        approved = parse_agparts_manifest(json.dumps(base))
        self.assertEqual(approved["F4J163707010"]["kaspi_pack_qty"], 4)
        entry = base["products"]["F4J163707010"]
        for invalid in (1, 2, 3, 5):
            with self.subTest(invalid_qty=invalid):
                entry["kaspi_pack_qty"] = invalid
                with self.assertRaises(ManifestError):
                    parse_agparts_manifest(json.dumps(base))
        entry["kaspi_pack_qty"] = 4
        entry["category"] = "oil"
        with self.assertRaises(ManifestError):
            parse_agparts_manifest(json.dumps(base))

    def test_no_manifest_and_unknown_fields_denied(self):
        with self.assertRaises(ManifestError):
            parse_agparts_manifest("")
        with self.assertRaises(ManifestError):
            parse_agparts_manifest('{"version":1,"products":{},"secret":"oops"}')


class CatalogApiTests(TestCase):
    def setUp(self):
        cache.clear()
        user = User.objects.create_user(username="agparts-api-local-test", password="local-test-secret")
        self.seller = SellerProfile.objects.create(
            user=user, name="AG Parts", slug="ag-parts", phone="0000000000", city="Алматы",
        )
        country = Country.objects.create(name="Testland")
        brand = Brand.objects.create(name="ExampleAuto", country=country)
        model = CarModel.objects.create(name="ExampleModel", brand=brand)
        self.product = Product.objects.create(
            title="Sample Product", article="000AG-TEST", price=3200,
            seller_profile=self.seller, seller_name="AG Parts",
            whatsapp_number="77700000000", city="Алматы", status="active",
            cost_price=1111, stock_qty=999, publish_to_kaspi=False,
            fitment_reviewed_on=timezone.localdate(),
            main_image="products/example-filter.png",
        )
        self.pp2, _ = Warehouse.objects.get_or_create(code="PP2", defaults={"name": "Fulfillment"})
        self.pp1, _ = Warehouse.objects.get_or_create(code="PP1", defaults={"name": "Reserve"})
        ProductWarehouseStock.objects.create(product=self.product, warehouse=self.pp2, quantity=4)
        ProductWarehouseStock.objects.create(product=self.product, warehouse=self.pp1, quantity=999)
        ProductFitment.objects.create(
            product=self.product, car_model=model,
            engine_policy="required", engine_code="ENGINE-TEST",
            year_from=2024, month_from=3, year_to=2025, month_to=11,
            market="KZ", body="Example body", generation="Example gen",
            fuel="gasoline", power_kw=90, note="Check month of production",
            status="confirmed", source_url="https://example.com/catalog",
            verified_on=timezone.localdate(),
        )
        self.manifest = {
            "version": 1,
            "products": {
                "000AG-TEST": {
                    "official_brand_verified": True,
                    "category": "cabin",
                    "title": {"ru": "Фильтр (тест)", "kk": "Сынақ сүзгісі", "zh": "测试滤清器"},
                    "supplier_confirmed_at": None,
                    "lead_time_days": None,
                    "kaspi_pack_qty": None,
                    "kaspi_master_sku": None,
                },
            },
        }

    def call(self, path=LIST_URL, *, token=TEST_TOKEN, manifest=None, method="get", enabled=True):
        raw = json.dumps(manifest if manifest is not None else self.manifest, ensure_ascii=False)
        with override_settings(AGPARTS_CATALOG_API_ENABLED=enabled,
                               AGPARTS_CATALOG_API_TOKEN=TEST_TOKEN,
                               AGPARTS_CATALOG_MANIFEST_JSON=raw):
            return getattr(self.client, method)(
                path, HTTP_AUTHORIZATION="Bearer " + token,
            )

    def test_opt_in_details_match_public_card_without_changing_v1_default(self):
        self.product.oem_cross_references = "OEM-001; OEM-001; OEM-002"
        self.product.description = "Public description"
        self.product.save(update_fields=["oem_cross_references", "description"])
        original = self.call().json()["products"][0]
        self.assertNotIn("details", original)
        response = self.call(LIST_URL + "?details=1")
        self.assertEqual(response.status_code, 200)
        details = response.json()["products"][0]["details"]
        self.assertEqual(details["oem_refs"], ["OEM-001", "OEM-002"])
        self.assertEqual(details["description"], "Public description")
        row = details["fitment_rows"][0]
        self.assertEqual(row["period"], "03.2024–11.2025")
        self.assertFalse(row["needs_check"])
        self.assertEqual(self.call(LIST_URL + "?details=invalid").status_code, 400)

    def test_unconfirmed_fitment_stays_marked_and_unsafe_source_is_removed(self):
        row = self.product.fitments.first()
        row.status = "candidate"
        row.source_url = "javascript:alert(1)"
        row.save(update_fields=["status", "source_url"])
        details = self.call(LIST_URL + "?details=1").json()["products"][0]["details"]
        self.assertTrue(details["fitment_rows"][0]["needs_check"])
        self.assertEqual(details["fitment_rows"][0]["source_url"], "")

    def test_feature_off_by_default(self):
        with override_settings(AGPARTS_CATALOG_API_ENABLED=False):
            response = self.client.get(LIST_URL, HTTP_AUTHORIZATION="Bearer " + TEST_TOKEN)
        self.assertEqual(response.status_code, 404)

    def test_authentication_and_get_only(self):
        self.assertEqual(self.call(token="wrong").status_code, 404)
        self.assertEqual(self.call(method="post").status_code, 405)
        with override_settings(AGPARTS_CATALOG_API_ENABLED=True,
                               AGPARTS_CATALOG_API_TOKEN="",
                               AGPARTS_CATALOG_MANIFEST_JSON=json.dumps(self.manifest)):
            self.assertEqual(self.client.get(LIST_URL).status_code, 503)

    def test_products_no_internal_fields(self):
        response = self.call()
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("noindex", response["X-Robots-Tag"])
        document = response.json()
        self.assertEqual(document["schema_version"], "agparts.catalog.v1")
        item = document["products"][0]
        self.assertEqual(item["article"], "000AG-TEST")
        self.assertIsNone(item["retail_price_kzt"])
        self.assertEqual(item["available_pp2_qty"], 4)
        self.assertEqual(item["availability"], "in_stock")
        self.assertEqual(item["category"], "cabin")
        self.assertIn("/products/products/example-filter.png", item["images"][0])
        for banned in ("cost_price", "seller_profile_id", "customer_phone",
                       "whatsapp_number", "stock_qty", "secret", "email", "password"):
            self.assertNotIn(banned, json.dumps(document))

    def test_fitment_preserves_month_and_qualifiers(self):
        response = self.call()
        self.assertEqual(response.status_code, 200)
        record = response.json()["products"][0]["confirmed_fitments"][0]
        self.assertEqual(record["month_from"], 3)
        self.assertEqual(record["month_to"], 11)
        self.assertEqual(record["market"], "KZ")
        self.assertEqual(record["body"], "Example body")
        self.assertEqual(record["engine_code"], "ENGINE-TEST")

    def test_pp1_not_included_and_no_order_for_zero_pp2(self):
        ProductWarehouseStock.objects.filter(product=self.product, warehouse=self.pp2).update(quantity=0)
        response = self.call()
        self.assertEqual(response.status_code, 200)
        item = response.json()["products"][0]
        self.assertEqual(item["available_pp2_qty"], 0)
        self.assertEqual(item["availability"], "inquiry")

    def test_supplier_confirmation_separate_from_stock(self):
        ProductWarehouseStock.objects.filter(product=self.product, warehouse=self.pp2).update(quantity=0)
        self.manifest["products"]["000AG-TEST"]["supplier_confirmed_at"] = timezone.now().isoformat()
        self.manifest["products"]["000AG-TEST"]["lead_time_days"] = 10
        item = self.call().json()["products"][0]
        self.assertEqual(item["availability"], "supplier_confirmed")
        self.assertEqual(item["lead_time_days"], 10)

    def test_stale_pp2_keeps_approved_product_price_but_masks_stock(self):
        ProductWarehouseStock.objects.filter(product=self.product, warehouse=self.pp2).update(
            updated_at=timezone.now() - timedelta(days=2))
        response = self.call()
        self.assertEqual(response.status_code, 200)
        item = response.json()["products"][0]
        self.assertIsNone(item["retail_price_kzt"])
        self.assertEqual(item["article"], "000AG-TEST")
        self.assertEqual(item["availability"], "unknown")
        self.assertIsNone(item["available_pp2_qty"])
        self.assertIsNone(item["stock_as_of"])
        self.assertIsNone(item["supplier_confirmed_at"])
        self.assertIsNone(item["lead_time_days"])
        self.assertNotIn(999, item.values())  # PP1 + old Product.stock_qty

    def test_stale_zero_with_supplier_confirmation_must_still_be_unknown(self):
        ProductWarehouseStock.objects.filter(product=self.product, warehouse=self.pp2).update(
            quantity=0, updated_at=timezone.now() - timedelta(days=2))
        self.manifest["products"]["000AG-TEST"]["supplier_confirmed_at"] = timezone.now().isoformat()
        self.manifest["products"]["000AG-TEST"]["lead_time_days"] = 10
        item = self.call().json()["products"][0]
        self.assertEqual(item["availability"], "unknown")
        self.assertIsNone(item["available_pp2_qty"])
        self.assertIsNone(item["supplier_confirmed_at"])
        self.assertIsNone(item["lead_time_days"])

    def test_missing_pp2_record_is_unknown_not_zero(self):
        ProductWarehouseStock.objects.filter(product=self.product, warehouse=self.pp2).delete()
        response = self.call()
        self.assertEqual(response.status_code, 200)
        item = response.json()["products"][0]
        self.assertEqual(item["availability"], "unknown")
        self.assertIsNone(item["available_pp2_qty"])
        self.assertIsNone(item["stock_as_of"])
        self.assertIsNone(item["retail_price_kzt"])

    def test_missing_pp2_with_supplier_confirmation_is_not_preorder(self):
        ProductWarehouseStock.objects.filter(product=self.product, warehouse=self.pp2).delete()
        self.manifest["products"]["000AG-TEST"]["supplier_confirmed_at"] = timezone.now().isoformat()
        self.manifest["products"]["000AG-TEST"]["lead_time_days"] = 7
        item = self.call().json()["products"][0]
        self.assertEqual(item["availability"], "unknown")
        self.assertIsNone(item["supplier_confirmed_at"])
        self.assertIsNone(item["lead_time_days"])

    def test_hidden_product_not_exposed(self):
        self.product.status = "hidden"
        self.product.save(update_fields=["status"])
        self.assertEqual(self.call().status_code, 503)

    def test_approved_seller_only_and_no_unlisted_product(self):
        seller_other = SellerProfile.objects.create(
            user=User.objects.create_user(username="unrelated-company"),
            name="Other", slug="other-company", phone="11111111", city="Астана",
        )
        extra = Product.objects.create(
            title="Other product", article="OTHER-TEST", seller_profile=seller_other,
            seller_name="Other", whatsapp_number="123", status="active", price=100,
        )
        self.assertEqual(self.call().json()["products"][0]["id"], self.product.pk)
        self.assertEqual(self.call(f"/internal/agparts/v1/products/{extra.pk}/").status_code, 404)

    def test_manifest_bogus_category_and_product_denied(self):
        self.manifest["products"]["000AG-TEST"]["category"] = "suspension"
        self.assertEqual(self.call().status_code, 503)

    def test_vehicles_are_advisory_not_auto_compatible(self):
        response = self.call(VEHICLES_URL)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["selection_ready"])
        self.assertEqual(body["fitments"][0]["month_from"], 3)
        self.assertEqual(body["fitments"][0]["product_id"], self.product.pk)

    def test_request_does_not_mutate_product_or_stocks(self):
        before = self.product.price
        before_stock = ProductWarehouseStock.objects.get(product=self.product, warehouse=self.pp2).quantity
        self.assertEqual(self.call().status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, before)
        self.assertEqual(ProductWarehouseStock.objects.get(product=self.product, warehouse=self.pp2).quantity, before_stock)

    def test_no_public_unauthenticated_data(self):
        with override_settings(AGPARTS_CATALOG_API_ENABLED=True,
                               AGPARTS_CATALOG_API_TOKEN=TEST_TOKEN,
                               AGPARTS_CATALOG_MANIFEST_JSON=json.dumps(self.manifest)):
            for path in (LIST_URL, VEHICLES_URL, f"/internal/agparts/v1/products/{self.product.pk}/"):
                with self.subTest(path=path):
                    self.assertEqual(self.client.get(path).status_code, 404)


    def test_unknown_engine_does_not_become_universal(self):
        ProductFitment.objects.filter(product=self.product).update(
            engine_policy="unknown", engine_code=""
        )
        result = self.call().json()
        self.assertEqual(result["products"][0]["confirmed_fitments"], [])

    def test_kaspi_link_uses_exact_fresh_merchant_offer_not_export_flags(self):
        url = ("https://kaspi.kz/shop/p/example-filter-12345678/"
               "?c=750000000&m=30363568&ms=true")
        listing = ProductKaspiListing.objects.create(
            product=self.product,
            master_sku="EXAMPLE-MASTER", merchant_sku=self.product.article,
            is_active=True,
            publish_to_kaspi=False,
            public_url=url,
            public_url_verified_at=timezone.now(),
            last_synced_at=timezone.now(),
            last_known_our_price=self.product.price,
        )
        KaspiCompetitorOfferSnapshot.objects.create(
            listing=listing, seller_code="30363568", seller_name="AG Parts",
            price=4100, is_available=True, source="office_collector", captured_at=timezone.now(),
        )
        # A stored URL, even a verified one, is not a buy link until the
        # owner has approved exact Kaspi master SKU and package quantity.
        with override_settings(AGPARTS_KASPI_MERCHANT_ID="30363568"):
            self.assertIsNone(self.call().json()["products"][0]["kaspi_public_url"])
            item = self.manifest["products"]["000AG-TEST"]
            item["kaspi_pack_qty"] = 1
            item["kaspi_master_sku"] = "EXAMPLE-MASTER"
            response = self.call()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["products"][0]["kaspi_public_url"], url)
        self.assertEqual(response.json()["products"][0]["kaspi_pack_qty"], 1)
        self.assertEqual(response.json()["products"][0]["retail_price_kzt"], 4100)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, 3200)
        # The export switches remain false and are independent of the link.
        self.assertFalse(self.product.publish_to_kaspi)
        self.assertFalse(listing.publish_to_kaspi)

    def test_kaspi_link_rejects_stale_price_wrong_merchant_and_pack_mismatch(self):
        owner = self.manifest["products"]["000AG-TEST"]
        owner["kaspi_master_sku"] = "EXAMPLE-MASTER"
        owner["kaspi_pack_qty"] = 4
        url = ("https://kaspi.kz/shop/p/example-filter-12345678/"
               "?c=750000000&m=30363568&ms=true")
        listing = ProductKaspiListing.objects.create(
            product=self.product, master_sku="EXAMPLE-MASTER", merchant_sku=self.product.article,
            is_active=True, public_url=url,
            public_url_verified_at=timezone.now(),
            last_synced_at=timezone.now() - timedelta(days=4),
            last_known_our_price=self.product.price,
        )
        with override_settings(AGPARTS_KASPI_MERCHANT_ID="30363568"):
            # Old Kaspi observation cannot authorize a current buy button.
            self.assertIsNone(self.call().json()["products"][0]["kaspi_public_url"])
            listing.last_synced_at = timezone.now()
            listing.last_known_our_price = self.product.price + 100
            listing.save(update_fields=["last_synced_at", "last_known_our_price"])
            # Our site price must match the observed price for the exact pack.
            self.assertIsNone(self.call().json()["products"][0]["kaspi_public_url"])
            listing.last_known_our_price = self.product.price
            listing.public_url = url.replace("m=30363568", "m=99999999")
            listing.save(update_fields=["last_known_our_price", "public_url"])
            self.assertIsNone(self.call().json()["products"][0]["kaspi_public_url"])

    def test_manifest_requires_matched_kaspi_listing_and_quantity(self):
        manifest = self.manifest["products"]["000AG-TEST"]
        manifest["kaspi_master_sku"] = "EXAMPLE-MASTER"
        self.assertEqual(self.call().status_code, 503)
        manifest["kaspi_pack_qty"] = 0
        self.assertEqual(self.call().status_code, 503)
        manifest["kaspi_pack_qty"] = 4
        self.assertEqual(self.call().status_code, 200)

    def test_four_pack_uses_own_snapshot_without_converting_stock_or_price(self):
        self.product.article = "F4J163707010"
        self.product.save(update_fields=["article"])
        entry = self.manifest["products"].pop("000AG-TEST")
        self.manifest["products"][self.product.article] = entry
        entry.update(category="spark", kaspi_pack_qty=4, kaspi_master_sku="FOUR-PACK")
        listing = ProductKaspiListing.objects.create(
            product=self.product, master_sku="FOUR-PACK", merchant_sku=self.product.article,
            is_active=True, public_url="https://kaspi.kz/shop/p/example-12345678/?m=30363568&ms=true",
            public_url_verified_at=timezone.now(),
        )
        own = KaspiCompetitorOfferSnapshot.objects.create(
            listing=listing, seller_code="30363568", seller_name="AG Parts",
            price=12450, is_available=True, source="office_collector", captured_at=timezone.now(),
        )
        KaspiCompetitorOfferSnapshot.objects.create(
            listing=listing, seller_code="other", seller_name="Other",
            price=100, is_available=True, source="office_collector", captured_at=timezone.now(),
        )
        with override_settings(AGPARTS_KASPI_MERCHANT_ID="30363568"):
            item = self.call().json()["products"][0]
            self.assertEqual(item["retail_price_kzt"], 12450)
            self.assertEqual(item["availability"], "unknown")
            self.assertIsNone(item["available_pp2_qty"])
            self.product.refresh_from_db()
            self.assertEqual(self.product.price, 3200)
            self.assertEqual(ProductWarehouseStock.objects.get(product=self.product, warehouse=self.pp2).quantity, 4)
            KaspiCompetitorOfferSnapshot.objects.filter(pk=own.pk).update(captured_at=timezone.now()-timedelta(days=2))
            item = self.call().json()["products"][0]
            self.assertIsNone(item["retail_price_kzt"])
            self.assertIsNone(item["kaspi_public_url"])
            KaspiCompetitorOfferSnapshot.objects.filter(pk=own.pk).update(captured_at=timezone.now(), is_available=False)
            self.assertIsNone(self.call().json()["products"][0]["retail_price_kzt"])

    def test_signed_cursor_rejects_snapshot_changes(self):
        second = Product.objects.create(
            title="Second test item", article="000AG-SECOND",
            seller_profile=self.seller, seller_name="AG Parts", whatsapp_number="00",
            status="active", price=1500, city="Алматы",
        )
        ProductWarehouseStock.objects.create(product=second, warehouse=self.pp2, quantity=2)
        self.manifest["products"]["000AG-SECOND"] = {
            "official_brand_verified": True,
            "category": "oil",
            "title": {"ru": "Тест второй", "kk": "Екінші сынақ", "zh": "第二个测试"},
            "supplier_confirmed_at": None,
            "lead_time_days": None,
                    "kaspi_pack_qty": None,
                    "kaspi_master_sku": None,
        }
        with patch("catalog.agparts_api.PAGE_SIZE", 1):
            first = self.call()
            self.assertEqual(first.status_code, 200)
            cursor = first.json()["next_cursor"]
            self.assertTrue(cursor)
            second_page = self.call(LIST_URL + "?cursor=" + cursor)
            self.assertEqual(second_page.status_code, 200)
            self.assertIsNone(second_page.json()["next_cursor"])
            ProductWarehouseStock.objects.filter(product=second, warehouse=self.pp2).update(quantity=3)
            self.assertEqual(self.call(LIST_URL + "?cursor=" + cursor).status_code, 409)

    def test_invalid_cursor_is_rejected(self):
        self.assertEqual(self.call(LIST_URL + "?cursor=not-a-signed-token").status_code, 409)

