import json
from datetime import timedelta
from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import CatalogImportBatch, Product, ProductKaspiListing, SellerProfile
from repricer.models import (
    KaspiActiveExportRow, KaspiActiveCompetitorOffer, KaspiRepricerSearchSettings,
)


class ApifyOutputTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("staff-output", is_staff=True)
        seller = SellerProfile.objects.create(
            user=self.staff, name="AG Parts", phone="+77000000000"
        )
        now = timezone.now()
        self.product = Product.objects.create(
            title="Масляный фильтр 1017110XEN01", article="1017110XEN01",
            seller_profile=seller, seller_name="AG Parts", whatsapp_number="+77000000000",
            stock_qty=7, cost_price=800,
        )
        self.listing = ProductKaspiListing.objects.create(
            product=self.product, master_sku="1017110XEN01",
            last_known_our_price=2700,
            last_known_kaspi_qty=5,
            public_url="https://kaspi.kz/shop/p/masljanyi-fil-tr-1017110xen01-141407587/",
            public_url_verified_at=now,
        )
        self.batch = CatalogImportBatch.objects.create(
            seller_profile=seller,
            source=CatalogImportBatch.SOURCE_KASPI_LISTING_FACTS,
            filename="ACTIVE.xlsx", file_sha256="f" * 64,
            started_at=now-timedelta(seconds=1), finished_at=now,
            mode=CatalogImportBatch.MODE_WRITE,
            source_scope=CatalogImportBatch.SCOPE_FULL,
            status=CatalogImportBatch.STATUS_SUCCESS,
            source_row_count=2,
        )
        self.active = KaspiActiveExportRow.objects.create(
            batch=self.batch, row_number=2, master_sku="1017110XEN01",
            product_name=self.product.title,
            listing=self.listing, price=2700, quantity=5, status="UPDATED",
        )
        self.unknown = KaspiActiveExportRow.objects.create(
            batch=self.batch, row_number=3, master_sku="UNMAPPED",
            product_name="Воздушный фильтр не подтвержден", brand="",
            price=5000, quantity=2, status="MISSING_LISTING",
        )
        self.client.force_login(self.staff)
        KaspiRepricerSearchSettings.objects.create(
            pk=1, cities=["Almaty", "Astana"], cards_per_product=2,
        )

    def _upload(self, entries):
        payload = SimpleUploadedFile(
            "result.json", json.dumps(entries, ensure_ascii=False).encode("utf8"),
            content_type="application/json",
        )
        return self.client.post(reverse("repricer:apify_output"), {
            "apify_output": payload,
        })

    def test_minimum_competitor_price_and_own_seller_exclusion(self):
        response = self._upload([
            {
                "id": "141407587",
                "title": self.product.title,
                "url": self.listing.public_url,
                "city": "Almaty",
                "offers": [
                    {"sellerId": "30363568", "sellerName": "AG Parts", "price": 1800},
                    {"sellerId": "another-1", "sellerName": "Store A", "price": 2400},
                    {"sellerId": "another-2", "sellerName": "Store B", "price": 2200},
                ],
            },
            {
                "id": "141407587",
                "title": self.product.title,
                "url": self.listing.public_url,
                "city": "Astana",
                "offers": [{"sellerId": "another-3", "sellerName": "Store C", "price": 2100}],
            },
        ])
        self.assertRedirects(response, reverse("repricer:dashboard"))
        self.active.refresh_from_db()
        self.listing.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.active.competitor_min_price, 2100)
        self.assertEqual(self.active.competitor_city, "Astana")
        self.assertEqual(self.active.competitor_seller, "Store C")
        self.assertEqual(KaspiActiveCompetitorOffer.objects.count(), 3)
        self.assertEqual(self.listing.last_known_our_price, 2700)
        self.assertEqual(self.product.stock_qty, 7)
        self.assertIsNone(self.unknown.competitor_min_price)
        dashboard = self.client.get(reverse("repricer:dashboard"))
        self.assertContains(dashboard, "Store C")
        detail = self.client.get(reverse("repricer:product", args=[self.listing.pk]))
        self.assertContains(detail, "Store C")

    def test_unmatched_or_bad_city_cannot_write_fake_price(self):
        response = self._upload([
            {"id": "88888888", "title": self.product.title, "city": "Aktobe",
             "offers": [{"sellerId": "someone", "price": 100}]},
            {"id": "88888888", "title": "Совершенно другой товар", "city": "Almaty",
             "offers": [{"sellerId": "someone", "price": 100}]},
        ])
        self.assertRedirects(response, reverse("repricer:dashboard"))
        self.active.refresh_from_db()
        self.assertIsNone(self.active.competitor_min_price)
        self.assertEqual(KaspiActiveCompetitorOffer.objects.count(), 0)

    def test_search_card_reordered_title_matches_unique_part_code(self):
        unmatched = KaspiActiveExportRow.objects.create(
            batch=self.batch, row_number=4,
            master_sku="115651957_689543859",
            product_name="FORGA стойка стабилизатора F1207022",
            brand="forga", price=1900, status="MISSING_LISTING",
        )
        response = self._upload([{
            "id": "115651957",
            "title": "Стойка стабилизатора FORGA F1207022",
            "brand": "FORGA",
            "url": "https://kaspi.kz/shop/p/forga-stoika-f1207022-115651957/",
            "city": "Almaty",
            "offers": [
                {"sellerId": "other-store", "sellerName": "Other Store", "price": 1600}
            ],
        }])
        self.assertRedirects(response, reverse("repricer:dashboard"))
        unmatched.refresh_from_db()
        self.assertEqual(unmatched.competitor_min_price, 1600)
        self.assertEqual(unmatched.competitor_scan_status, "MATCHED")
        self.assertIsNone(self.unknown.competitor_min_price)

    def test_search_card_rejects_wrong_part_article(self):
        wrong = KaspiActiveExportRow.objects.create(
            batch=self.batch, row_number=4,
            master_sku="115651957_689543859",
            product_name="FORGA стойка стабилизатора F1207022",
            brand="forga", price=1900, status="MISSING_LISTING",
        )
        self._upload([{
            "id": "115651957",
            "title": "Стойка стабилизатора FORGA F1207033",
            "brand": "FORGA",
            "city": "Almaty",
            "offers": [{"sellerId": "other-store", "price": 1200}],
        }])
        wrong.refresh_from_db()
        self.assertIsNone(wrong.competitor_min_price)

    def test_empty_offers_are_diagnosed_separately(self):
        self._upload([{
            "id": "141407587",
            "title": self.product.title,
            "url": self.listing.public_url,
            "city": "Almaty",
            "offers": [],
        }])
        self.active.refresh_from_db()
        self.assertEqual(self.active.competitor_scan_status, "NO_OFFERS")
        self.assertIn("пустой список", self.active.competitor_scan_note)

    def test_own_only_is_not_competition(self):
        self._upload([{
            "id": "141407587",
            "title": self.product.title,
            "url": self.listing.public_url,
            "city": "Almaty",
            "offers": [{"sellerId": "30363568", "sellerName": "AG Parts", "price": 100}],
        }])
        self.active.refresh_from_db()
        self.assertIsNone(self.active.competitor_min_price)
        self.assertIn("Только предложения", self.active.competitor_scan_note)

    def test_clear_only_competitor_evidence(self):
        self._upload([{
            "id": "141407587", "title": self.product.title,
            "city": "Almaty", "url": self.listing.public_url,
            "offers": [{"sellerId": "someone", "price": 2300}],
        }])
        self.assertEqual(KaspiActiveCompetitorOffer.objects.count(), 1)
        response = self.client.post(reverse("repricer:apify_clear"))
        self.assertRedirects(response, reverse("repricer:dashboard"))
        self.active.refresh_from_db()
        self.listing.refresh_from_db()
        self.assertIsNone(self.active.competitor_min_price)
        self.assertEqual(self.active.price, 2700)
        self.assertEqual(self.listing.last_known_our_price, 2700)
        self.assertEqual(KaspiActiveCompetitorOffer.objects.count(), 0)

    def test_nonstaff_cannot_import(self):
        self.client.force_login(User.objects.create_user("not-staff"))
        self.assertNotEqual(self._upload([]).status_code, 200)
