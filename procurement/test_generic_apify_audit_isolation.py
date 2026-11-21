"""Generic comparisons must not silently borrow unverified Apify evidence."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (
    KaspiPriceAudit, Membership, Offer, PermanentBuyingItem, Supplier,
)


class GenericComparisonIsolationTests(TestCase):
    def test_historical_comparison_ignores_pending_november_export(self):
        person = get_user_model().objects.create_user(
            username="comparison_viewer", password="test-only-login")
        Membership.objects.create(user=person, role="user")
        supplier = Supplier.objects.create(name="Omega")
        offer = Offer.objects.create(
            supplier=supplier, brand="STINSS", article="S2S1552",
            name="Амортизатор передний", cost=Decimal("5603"), stock="4")
        PermanentBuyingItem.objects.create(
            offer=offer, source="historical", product_id="old-card-id",
            title="Исторический товар",
            cost_snapshot=Decimal("5603"),
            kaspi_price_snapshot=Decimal("11000"),
            commission_snapshot=Decimal("12.5"),
            expenses_snapshot=Decimal("250"),
            profit_snapshot=Decimal("3772"),
            roi_snapshot=Decimal("67"))
        export = "dataset_kaspi-api_2026-11-21_14-00-09-125.json"
        KaspiPriceAudit.objects.create(
            offer=offer, source_filename=export,
            product_id="179049603", min_price=Decimal("12784"),
            card_url="https://kaspi.kz/shop/p/test/",
            cost_snapshot=Decimal("5603"),
            commission_snapshot=Decimal("12.5"),
            expenses_snapshot=Decimal("250"),
            profit=Decimal("5331"), roi=Decimal("60"),
            identity_status="needs_review")
        self.client.force_login(person)
        response = self.client.get(reverse("procurement:comparison", args=[offer.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_product_id"], "old-card-id")
        self.assertNotEqual(response.context["comparison_source"], export)
        self.assertNotEqual(response.context["kaspi_price"], Decimal("12784"))
