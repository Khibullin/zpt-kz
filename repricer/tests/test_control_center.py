from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import Product, ProductKaspiListing
from repricer.models import KaspiManualPriceDraft


@override_settings(KASPI_OWN_MERCHANT_IDS="30363568")
class PriceControlCenterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("staff-prices", password="test", is_staff=True)
        self.product = Product.objects.create(
            title="Тормозной диск", article="ABC-101",
            seller_name="AG Parts", whatsapp_number="+77000000000",
            cost_price=8000, stock_qty=7,
        )
        self.listing = ProductKaspiListing.objects.create(
            product=self.product, master_sku="100101", merchant_sku="ABC-101",
            last_known_our_price=12000, last_known_kaspi_qty=6,
        )
        self.dashboard = reverse("repricer:dashboard")
        self.detail = reverse("repricer:product", args=[self.listing.pk])

    def test_only_staff_can_see_prices(self):
        self.assertNotEqual(self.client.get(self.dashboard).status_code, 200)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.dashboard).status_code, 200)

    def test_table_links_to_separate_details(self):
        self.client.force_login(self.user)
        response = self.client.get(self.dashboard)
        self.assertContains(response, "Тормозной диск")
        self.assertContains(response, self.detail)
        self.assertNotContains(response, "Предложения конкурентов на Kaspi")
        self.assertContains(self.client.get(self.detail), "Предложения конкурентов на Kaspi")

    def test_inline_input_and_output_workflow_is_present(self):
        self.client.force_login(self.user)
        response = self.client.get(self.dashboard)
        self.assertContains(response, "Подготовка задания Apify")
        self.assertContains(response, "Обработка результатов Apify")
        self.assertContains(response, 'name="kaspi_file"')
        self.assertContains(response, 'name="apify_output"')
        self.assertContains(response, "Полный мониторинг — JSON для всех")
        self.assertContains(response, "Карточек на запрос")
        self.assertContains(response, "Правила")
        self.assertContains(response, reverse("repricer:rules"))
        self.assertContains(response, "Сохранить изменения")

    def test_manual_draft_does_not_change_live_price(self):
        self.client.force_login(self.user)
        url = reverse("repricer:save_price", args=[self.listing.pk])
        response = self.client.post(url, {"price": "11900"})
        self.assertRedirects(response, self.detail)
        self.assertEqual(KaspiManualPriceDraft.objects.get(listing=self.listing).price, 11900)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.last_known_our_price, 12000)

    def test_invalid_price_rejected(self):
        self.client.force_login(self.user)
        self.client.post(reverse("repricer:save_price", args=[self.listing.pk]),
                         {"price": "-100"})
        self.assertFalse(KaspiManualPriceDraft.objects.exists())

    def test_price_control_pagination_links(self):
        self.client.force_login(self.user)
        ProductKaspiListing.objects.bulk_create([
            ProductKaspiListing(product=self.product, master_sku=f"extra-{i}")
            for i in range(45)
        ])
        response = self.client.get(self.dashboard + "?page=1")
        self.assertContains(response, "page=2")
        self.assertContains(response, "page=3")
        self.assertContains(response, "Первая страница" if False else "Вперёд")

    def test_active_file_defines_all_rows_including_unmapped_sku(self):
        from io import BytesIO
        from django.core.files.uploadedfile import SimpleUploadedFile
        from openpyxl import Workbook
        from catalog.models import SellerProfile
        from repricer.models import KaspiActiveExportRow
        seller = SellerProfile.objects.create(
            user=self.user, name="AG Parts", phone="+77000000000",
        )
        self.product.seller_profile = seller
        self.product.save(update_fields=["seller_profile"])
        wb = Workbook()
        wb.active.append(["SKU", "price", "PP2", "model"])
        wb.active.append([self.listing.master_sku, 14000, 4, "Наша запчасть"])
        wb.active.append(["NOT-MAPPED-777", 16000, 2, "Неизвестный товар"])
        data = BytesIO()
        wb.save(data)
        self.client.force_login(self.user)
        upload = SimpleUploadedFile("ACTIVE.xlsx", data.getvalue())
        response = self.client.post(reverse("repricer:upload_kaspi"), {"kaspi_file": upload})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(KaspiActiveExportRow.objects.count(), 2)
        dashboard = self.client.get(self.dashboard)
        self.assertContains(dashboard, "Неизвестный товар")
        self.assertContains(dashboard, "NOT-MAPPED-777")
        self.assertEqual(dashboard.context["summary"]["total"], 2)
        self.assertEqual(dashboard.context["summary"]["unmatched"], 1)
        from repricer.models import KaspiActiveExportRow
        self.assertEqual(KaspiActiveExportRow.objects.get(master_sku="NOT-MAPPED-777").product_name, "Неизвестный товар")
        self.assertNotContains(dashboard, "extra-unrelated-listing")

    def test_upload_kaspi_file_refreshes_only_observed_listing_data(self):
        from io import BytesIO
        from django.core.files.uploadedfile import SimpleUploadedFile
        from openpyxl import Workbook
        from catalog.models import SellerProfile, ProductWarehouseStock
        seller = SellerProfile.objects.create(
            user=self.user, name="AG Parts", phone="+77000000000",
        )
        self.product.seller_profile = seller
        self.product.save(update_fields=["seller_profile"])
        workbook = Workbook()
        workbook.active.append(["SKU", "price", "PP2"])
        workbook.active.append([self.listing.master_sku, 17000, 9])
        data = BytesIO()
        workbook.save(data)
        upload = SimpleUploadedFile(
            "active.xlsx", data.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("repricer:upload_kaspi"), {"kaspi_file": upload},
        )
        self.assertRedirects(response, self.dashboard)
        self.listing.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.listing.last_known_our_price, 17000)
        self.assertEqual(self.listing.last_known_kaspi_qty, 9)
        self.assertEqual(self.product.cost_price, 8000)
        self.assertEqual(self.product.stock_qty, 7)
        self.assertEqual(self.product.price, None)
        self.assertEqual(ProductWarehouseStock.objects.count(), 0)
        self.assertContains(self.client.get(self.dashboard), "Обновить ассортимент ACTIVE.xlsx")

    def test_xlsx_download_contains_saved_draft(self):
        self.client.force_login(self.user)
        KaspiManualPriceDraft.objects.create(listing=self.listing, price=11900, updated_by=self.user)
        response = self.client.get(reverse("repricer:export"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml.sheet", response["Content-Type"])
        from io import BytesIO
        from openpyxl import load_workbook
        workbook = load_workbook(BytesIO(response.content), read_only=True)
        self.assertEqual(workbook.active["F2"].value, 11900)

    def test_xlsx_without_drafts_downloads_full_table(self):
        from io import BytesIO
        from openpyxl import load_workbook
        self.client.force_login(self.user)
        response = self.client.get(reverse("repricer:export"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response["Content-Disposition"])
        sheet = load_workbook(BytesIO(response.content)).active
        self.assertEqual(sheet["C2"].value, "ABC-101")
        self.assertIsNone(sheet["F2"].value)
        self.assertFalse(KaspiManualPriceDraft.objects.exists())

    def test_xlsx_requires_staff(self):
        self.assertEqual(self.client.get(reverse("repricer:export")).status_code, 302)
        self.client.force_login(User.objects.create_user("ordinary"))
        self.assertEqual(self.client.get(reverse("repricer:export")).status_code, 302)
