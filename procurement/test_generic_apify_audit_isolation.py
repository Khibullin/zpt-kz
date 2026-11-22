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

    def test_crossquery_audit_stays_staff_only_and_out_of_generic_detail(self):
        viewer = get_user_model().objects.create_user(
            username="crossquery_viewer", password="test-only-login")
        Membership.objects.create(user=viewer, role="user")
        staff = get_user_model().objects.create_user(
            username="crossquery_staff", password="test-only-login")
        Membership.objects.create(user=staff, role="sales")
        supplier = Supplier.objects.create(name="Omega")
        offer = Offer.objects.create(
            supplier=supplier, brand="RAMEDER", article="20A040137",
            name="Амортизатор передний", cost=Decimal("9523"), stock="3")
        PermanentBuyingItem.objects.create(
            offer=offer, source="historical", product_id="older-card",
            title="Сохранённый товар", cost_snapshot=Decimal("9523"),
            kaspi_price_snapshot=Decimal("18000"),
            commission_snapshot=Decimal("12.5"),
            expenses_snapshot=Decimal("250"),
            profit_snapshot=Decimal("5977"), roi_snapshot=Decimal("62"))
        export = (
            "dataset_kaspi-api_2026-11-21_13-39-32-482(2).json"
            "#crossquery-20261122"
        )
        KaspiPriceAudit.objects.create(
            offer=offer, source_filename=export, product_id="143040612",
            min_price=Decimal("17975"), cost_snapshot=Decimal("9523"),
            card_title="RAMEDER 20A040137 1 шт",
            commission_snapshot=Decimal("12.5"),
            expenses_snapshot=Decimal("250"),
            profit=Decimal("5955"), roi=Decimal("62.53"),
            identity_status="needs_review")

        url = reverse("procurement:comparison", args=[offer.pk])
        self.client.force_login(viewer)
        ordinary = self.client.get(url)
        self.assertEqual(ordinary.status_code, 200)
        self.assertEqual(ordinary.context["selected_product_id"], "older-card")
        self.assertNotEqual(ordinary.context["kaspi_price"], Decimal("17975"))
        self.assertNotEqual(ordinary.context["comparison_source"], export)
        restricted = self.client.get(url, {"apify_source": export})
        self.assertEqual(restricted.status_code, 403)

        self.client.force_login(staff)
        staff_detail = self.client.get(url, {"apify_source": export})
        self.assertEqual(staff_detail.status_code, 200)
        self.assertEqual(staff_detail.context["selected_product_id"], "143040612")
        self.assertTrue(staff_detail.context["review_only"])
        self.assertEqual(staff_detail.context["kaspi_price"], Decimal("17975"))
