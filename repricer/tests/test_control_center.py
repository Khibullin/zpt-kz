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

    def test_monitor_button_is_real_collector_form(self):
        self.client.force_login(self.user)
        response = self.client.get(self.dashboard)
        self.assertContains(response, "Провести мониторинг цен")
        self.assertContains(response, 'id="manual-price-form"')
        self.assertContains(response, 'id="manual-price-start"')
        self.assertContains(response, 'id="manual-price-progress"')
        self.assertContains(response, 'id="manual-price-pause"')
        self.assertContains(response, 'id="manual-price-resume"')
        self.assertContains(response, 'value="all" selected')

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
