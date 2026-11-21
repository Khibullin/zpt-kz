from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from catalog.models import Product, ProductKaspiListing
from integrations.kaspi_competitors import (
    KaspiCompetitorOffer, CompetitorPriceSourceRateLimited,
)
from repricer.models import (
    KaspiManualCollectionRun, KaspiCompetitorOfferSnapshot,
    KaspiCompetitorIngestBatch, KaspiRepricerRecommendation,
)

START = "/office/repricer/manual/start/"


class ManualCollectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="collector-staff", password="not-used", is_staff=True,
        )
        self.normal = User.objects.create_user(
            username="normal-buyer", password="not-used",
        )
        self.product = Product.objects.create(
            title="Verified part", article="ABC-100", seller_name="AG Parts",
            whatsapp_number="+77000000000",
        )
        self.listing = ProductKaspiListing.objects.create(
            product=self.product,
            master_sku="12345678_12345",
            merchant_sku="ABC-100",
            is_active=True, last_known_our_price=7000,
            last_known_kaspi_qty=2,
        )

    def test_nonstaff_cannot_launch(self):
        self.client.force_login(self.normal)
        response = self.client.post(START, {"city_id": "750000000"})
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(KaspiManualCollectionRun.objects.count(), 0)

    def test_unknown_city_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post(START, {"city_id": "bad"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(KaspiManualCollectionRun.objects.count(), 0)

    def test_collection_records_city_and_never_updates_our_prices(self):
        self.client.force_login(self.user)
        response = self.client.post(START, {"city_id": "710000000"})
        self.assertEqual(response.status_code, 200)
        run = response.json()["id"]
        offer = KaspiCompetitorOffer(
            seller_name="Another seller", seller_code="other-1",
            price=Decimal("6500"), position=1, is_available=True,
        )
        with patch("repricer.manual_collection.KaspiPublicOfferSource") as source:
            source.return_value.fetch_offers.return_value = [offer]
            step = self.client.post(f"/office/repricer/manual/{run}/step/")
        self.assertEqual(step.status_code, 200)
        self.assertEqual(step.json()["status"], "done")
        self.assertEqual(step.json()["saved_offers"], 1)
        self.assertEqual(KaspiCompetitorOfferSnapshot.objects.get().city_id, "710000000")
        self.assertEqual(KaspiCompetitorIngestBatch.objects.get().city_id, "710000000")
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.last_known_our_price, 7000)
        self.assertEqual(KaspiRepricerRecommendation.objects.count(), 0)

    def test_rate_limit_stops_collection_without_fake_fresh_data(self):
        self.client.force_login(self.user)
        run = self.client.post(START, {"city_id": "750000000"}).json()["id"]
        with patch("repricer.manual_collection.KaspiPublicOfferSource") as source:
            source.return_value.fetch_offers.side_effect = CompetitorPriceSourceRateLimited(
                "Kaspi HTTP 429", http_status=429,
            )
            response = self.client.post(f"/office/repricer/manual/{run}/step/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "stopped")
        self.assertEqual(response.json()["processed"], 0)
        self.assertEqual(KaspiCompetitorOfferSnapshot.objects.count(), 0)
        self.assertEqual(KaspiCompetitorIngestBatch.objects.count(), 0)

    def test_pause_and_resume(self):
        self.client.force_login(self.user)
        run = self.client.post(START, {"city_id": "750000000"}).json()["id"]
        paused = self.client.post(f"/office/repricer/manual/{run}/pause/").json()
        self.assertEqual(paused["status"], "stopped")
        resumed = self.client.post(f"/office/repricer/manual/{run}/resume/").json()
        self.assertEqual(resumed["status"], "running")
