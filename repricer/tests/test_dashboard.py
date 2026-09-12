from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import Product, ProductKaspiListing
from repricer.models import KaspiRepricerRule


class KaspiRepricerDashboardTests(TestCase):
    def setUp(self):
        self.url = reverse("repricer:dashboard")
        self.product = Product.objects.create(
            title="Тестовый воздушный фильтр",
            article="AIR-TEST-001",
            seller_name="AG Parts",
            whatsapp_number="+77000000000",
        )
        self.listing = ProductKaspiListing.objects.create(
            product=self.product,
            master_sku="KASPI-001",
            merchant_sku="AIR-TEST-001",
            last_known_our_price=12000,
        )
        KaspiRepricerRule.objects.create(
            listing=self.listing,
            min_price=Decimal("10000"),
        )

    def test_anonymous_user_is_redirected_to_admin_login(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_staff_user_can_open_dashboard(self):
        user = get_user_model().objects.create_user(
            username="repricer-staff",
            password="test-password-123",
            is_staff=True,
        )
        self.client.force_login(user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Репрайсер Kaspi")
        self.assertContains(response, "AIR-TEST-001")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
