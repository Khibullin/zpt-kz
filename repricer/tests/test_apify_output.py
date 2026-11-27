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
    def test_vehicle_make_in_title_does_not_override_different_manufacturer(self):
        from repricer.apify_output import parse_apify_offers
        row = KaspiActiveExportRow.objects.create(
            batch=self.batch, row_number=4, master_sku='865592695',
            product_name='Воздушный фильтр 151000151AA', brand='Chery',
            price=2910, quantity=1, status='MISSING_LISTING',
        )
        result = parse_apify_offers([{
            'id': '146393085', 'title': '555 воздушный фильтр Chery Tiggo 8 Pro MAX 151000151AA',
            'brand': '555', 'city': 'Almaty',
            'offers': [{'sellerId': 'other', 'price': 2695}],
        }], [row], ['Almaty'])
        self.assertEqual(result['evidence'], [])
        self.assertEqual(result['brand_candidates'][row.pk]['price'], 2695)
        self._upload([{
            'id': '146393085', 'title': '555 воздушный фильтр Chery Tiggo 8 Pro MAX 151000151AA',
            'brand': '555', 'city': 'Almaty',
            'url': 'https://kaspi.kz/shop/p/filter-151000151aa-146393085/',
            'offers': [{'sellerId': 'other', 'sellerName': 'Other', 'price': 2695}],
        }])
        row.refresh_from_db()
        self.assertEqual(row.candidate_price, 2695)
        self.assertIsNone(row.competitor_min_price)
        page = self.client.get(reverse('repricer:dashboard') + '?status=review')
        self.assertContains(page, row.master_sku)
        self.client.post(reverse('repricer:apify_clear'))
        row.refresh_from_db()
        self.assertIsNone(row.candidate_price)

    def test_year_range_does_not_match_camry_hood_support_to_accent(self):
        from repricer.apify_output import parse_apify_offers, _part_codes
        self.assertNotIn('20112017', _part_codes('Toyota Camry 2011-2017'))
        row = KaspiActiveExportRow.objects.create(
            batch=self.batch, row_number=4, master_sku='134895078_715785856',
            product_name='EEP 15A060040 упор капота для Toyota Camry 2011-2017 черный',
            brand='eep', price=5000, quantity=1, status='MISSING_LISTING',
        )
        result = parse_apify_offers([{
            'id': '108658070', 'title': 'ALK1200014 Hyundai Accent 2011-2017 черный',
            'brand': 'Без бренда', 'city': 'Almaty',
            'offers': [{'sellerId': 'other', 'price': 3498}],
        }], [row], ['Almaty'])
        self.assertEqual(result['evidence'], [])
        valid = parse_apify_offers([{
            'id': '134895078', 'title': row.product_name, 'brand': 'EEP', 'city': 'Almaty',
            'offers': [{'sellerId': 'other', 'price': 4500}],
        }], [row], ['Almaty'])
        self.assertEqual(valid['evidence'][0]['price'], 4500)

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

    def test_exact_article_unbranded_card_is_allowed_only_for_same_part_type(self):
        row = KaspiActiveExportRow.objects.create(
            batch=self.batch, row_number=4, master_sku="343875218",
            merchant_sku="M111109111", product_name="Воздушный фильтр M111109111",
            brand="Chery", price=2500, status="MISSING_LISTING",
        )
        self._upload([
            {
                "id": "161941852", "title": "Воздушный фильтр M111109111",
                "brand": "Без бренда", "city": "Almaty",
                "url": "https://kaspi.kz/shop/p/vozdushnyi-filtr-m111109111-161941852/",
                "offers": [{"sellerId": "5050005", "price": 3500}],
            },
            {
                "id": "161941853", "title": "Салонный фильтр M111109111",
                "brand": "Без бренда", "city": "Almaty",
                "offers": [{"sellerId": "5050006", "price": 100}],
            },
        ])
        row.refresh_from_db()
        self.assertEqual(row.competitor_min_price, 3500)

    def test_different_branded_part_is_not_automatically_identical(self):
        row = KaspiActiveExportRow.objects.create(
            batch=self.batch, row_number=4, master_sku="180634092",
            merchant_sku="D20T0120700",
            product_name="Свеча зажигания AG Parts D20T0120700",
            brand="ag-parts", price=4550, status="MISSING_LISTING",
        )
        self._upload([{
            "id": "167557785", "title": "Свеча зажигания TORCH D20T0120700",
            "brand": "TORCH", "city": "Almaty",
            "offers": [{"sellerId": "30422258", "price": 5000}],
        }])
        row.refresh_from_db()
        self.assertIsNone(row.competitor_min_price)

    def test_two_active_skus_with_same_identical_part_both_get_comparison(self):
        a = KaspiActiveExportRow.objects.create(
            batch=self.batch, row_number=4, master_sku="F081109111HD",
            product_name="AS воздушный фильтр Jetour DASHING",
            brand="ag-parts", price=2730, status="MISSING_LISTING",
        )
        b = KaspiActiveExportRow.objects.create(
            batch=self.batch, row_number=5, master_sku="323939926",
            merchant_sku="F081109111HD",
            product_name="AG Parts воздушный фильтр F081109111HD",
            brand="ag-parts", price=2730, status="MISSING_LISTING",
        )
        self._upload([{
            "id": "123456788",
            "title": "AG Parts воздушный фильтр F081109111HD",
            "brand": "AG Parts", "city": "Almaty",
            "offers": [{"sellerId": "competitor", "price": 3250}],
        }])
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.competitor_min_price, 3250)
        self.assertEqual(b.competitor_min_price, 3250)

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

    def test_four_distinct_price_statuses_are_explained(self):
        self._upload([{
            "id": "141407587", "title": self.product.title,
            "url": self.listing.public_url, "city": "Almaty",
            "offers": [{"sellerId": "30363568", "sellerName": "AG Parts", "price": 2700}],
        }])
        page = self.client.get(reverse("repricer:dashboard"))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["apify_scan_our_prices"], 2)
        self.assertEqual(page.context["apify_scan_only_own"], 1)
        self.assertEqual(page.context["apify_scan_unconfirmed"], 1)
        self.assertContains(page, "Только AG Parts в Output")
        self.assertContains(page, "Не подтверждена карточка")
        filtered = self.client.get(reverse("repricer:dashboard") + "?status=only_own")
        self.assertEqual(filtered.context["page"].paginator.count, 1)
        unconfirmed = self.client.get(reverse("repricer:dashboard") + "?status=unconfirmed")
        self.assertEqual(unconfirmed.context["page"].paginator.count, 1)

    def test_followup_output_does_not_erase_existing_confirmed_minimum(self):
        self._upload([{
            "id": "141407587", "url": self.listing.public_url,
            "title": self.product.title, "city": "Almaty",
            "offers": [{"sellerId": "other-shop", "price": 2200}],
        }])
        self.active.refresh_from_db()
        self.assertEqual(self.active.competitor_min_price, 2200)
        from django.core.files.uploadedfile import SimpleUploadedFile
        # A later discovery batch has only our own seller for that product:
        # it must not erase the earlier confirmed competitor's 2200.
        upload = SimpleUploadedFile("followup.json", json.dumps([{
            "id": "141407587", "url": self.listing.public_url,
            "title": self.product.title, "city": "Astana",
            "offers": [{"sellerId": "30363568", "price": 2000}],
        }]).encode("utf-8"))
        result = self.client.post(reverse("repricer:apify_output"), {
            "apify_output": upload, "merge": "1",
        })
        self.assertRedirects(result, reverse("repricer:dashboard"))
        self.active.refresh_from_db()
        self.assertEqual(self.active.competitor_min_price, 2200)
        self.assertEqual(KaspiActiveCompetitorOffer.objects.count(), 1)

    def test_followup_cheaper_offer_updates_minimum_without_editing_our_price(self):
        self._upload([{
            "id": "141407587", "url": self.listing.public_url,
            "title": self.product.title, "city": "Almaty",
            "offers": [{"sellerId": "other-shop", "price": 2200}],
        }])
        upload = SimpleUploadedFile("followup.json", json.dumps([{
            "id": "141407587", "url": self.listing.public_url,
            "title": self.product.title, "city": "Astana",
            "offers": [{"sellerId": "new-shop", "price": 1900}],
        }]).encode("utf-8"))
        self.client.post(reverse("repricer:apify_output"), {
            "apify_output": upload, "merge": "1",
        })
        self.active.refresh_from_db()
        self.listing.refresh_from_db()
        self.assertEqual(self.active.competitor_min_price, 1900)
        self.assertEqual(KaspiActiveCompetitorOffer.objects.count(), 2)
        self.assertEqual(self.listing.last_known_our_price, 2700)

    def test_nonstaff_cannot_import(self):
        self.client.force_login(User.objects.create_user("not-staff"))
        self.assertNotEqual(self._upload([]).status_code, 200)

    def test_price_control_export_all_pages_including_unmapped_without_drafts(self):
        from openpyxl import load_workbook
        KaspiActiveExportRow.objects.bulk_create([
            KaspiActiveExportRow(batch=self.batch, row_number=i + 4,
                                 master_sku=f"extra-{i}", product_name="Extra",
                                 status="MISSING_LISTING")
            for i in range(25)
        ])
        self.active.competitor_min_price = 2200
        self.active.competitor_scan_status = "MATCHED"
        self.active.save()
        response = self.client.get(reverse("repricer:export") + "?page=2")
        self.assertEqual(response.status_code, 200)
        sheet = load_workbook(BytesIO(response.content)).active
        self.assertEqual(sheet.max_row, 28)
        self.assertEqual(sheet["E2"].value, 2700)
        self.assertEqual(sheet["J2"].value, 2200)
        self.assertEqual(sheet["N2"].value, 500)
        self.assertEqual(sheet["B3"].value, "UNMAPPED")
        self.assertIsNone(sheet["F2"].value)
        self.assertIsNone(sheet["J3"].value)

    def test_price_control_export_preserves_filters_and_empty_results(self):
        from openpyxl import load_workbook
        url = reverse("repricer:export")
        for query, expected in [("?status=unmatched", "UNMAPPED"),
                                ("?q=1017110XEN01", "1017110XEN01")]:
            sheet = load_workbook(BytesIO(self.client.get(url + query).content)).active
            self.assertEqual(sheet.max_row, 2)
            self.assertEqual(sheet["B2"].value, expected)
        empty = self.client.get(url + "?q=nonexistent")
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(load_workbook(BytesIO(empty.content)).active.max_row, 1)
        dashboard = self.client.get(reverse("repricer:dashboard") + "?status=unmatched&q=UNMAPPED")
        self.assertContains(dashboard, url + "?status=unmatched&amp;q=UNMAPPED")
