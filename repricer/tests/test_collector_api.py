from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
import json

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from catalog.models import Product, ProductKaspiListing, StockMovement
from repricer.models import (
    KaspiCompetitorIngestBatch,
    KaspiCompetitorOfferSnapshot,
    KaspiOwnPriceSnapshot,
    KaspiRepricerRecommendation,
    KaspiRepricerRule,
)

TOKEN = "collector-test-token"
LISTINGS_URL = "/internal/kaspi/competitor-collector/listings/"
BATCHES_URL = "/internal/kaspi/competitor-collector/batches/"


def _product(article):
    return Product.objects.create(
        title=f"Title {article}",
        article=article,
        seller_name="Demo Seller",
        whatsapp_number="+77000000000",
    )


def _listing(article, master_sku, **kwargs):
    defaults = {
        "master_sku": master_sku,
        "merchant_sku": article,
        "is_active": True,
        "last_known_our_price": 3034,
        "last_known_kaspi_qty": 8,
        "public_url": "",
    }
    defaults.update(kwargs)
    return ProductKaspiListing.objects.create(product=_product(article), **defaults)


def _offer_payload(**kwargs):
    row = {
        "seller_name": "Other Shop",
        "seller_code": "30308762",
        "price": "3033.00",
        "position": 1,
        "is_available": True,
    }
    row.update(kwargs)
    return row


def _batch_payload(listing, offers=None, **kwargs):
    payload = {
        "batch_id": str(uuid4()),
        "listing_id": listing.pk,
        "master_sku": listing.master_sku,
        "captured_at": timezone.now().isoformat(),
        "offers": offers
        or [
            _offer_payload(
                seller_name="TEST-MERCHANT",
                seller_code="TEST-OWN",
                price="3034.00",
                position=2,
            ),
            _offer_payload(),
        ],
    }
    payload.update(kwargs)
    return payload


@override_settings(
    KASPI_COMPETITOR_COLLECTOR_TOKEN=TOKEN,
    ALLOWED_HOSTS=["*"],
    ROOT_URLCONF="backend.urls",
)
class CollectorApiTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="zpt.kz")
        self.listing = _listing("X0390000206", "115801437_271928151")

    def _auth(self, token=TOKEN):
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def _post(self, payload, **headers):
        return self.client.post(
            BATCHES_URL,
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(),
            **headers,
        )

    def test_manifest_without_token_is_401(self):
        response = self.client.get(LISTINGS_URL)
        self.assertEqual(response.status_code, 401)

    def test_manifest_wrong_token_is_401(self):
        response = self.client.get(LISTINGS_URL, **self._auth("wrong-token"))
        self.assertEqual(response.status_code, 401)

    def test_manifest_staff_session_is_not_enough(self):
        user = User.objects.create_user(
            username="staff-collector",
            password="secret-pass",
            is_staff=True,
        )
        self.client.force_login(user)
        response = self.client.get(LISTINGS_URL)
        self.assertEqual(response.status_code, 401)

    def test_manifest_returns_active_listings_only(self):
        inactive = _listing("INACTIVE", "999", is_active=False)
        response = self.client.get(LISTINGS_URL, **self._auth())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("listings", payload)
        rows = payload["listings"]
        ids = [row["listing_id"] for row in rows]
        self.assertIn(self.listing.pk, ids)
        self.assertNotIn(inactive.pk, ids)
        self.assertEqual(
            set(rows[0]),
            {
                "listing_id",
                "article",
                "master_sku",
                "merchant_sku",
                "last_known_our_price",
                "public_url",
            },
        )
        encoded = json.dumps(payload)
        self.assertNotIn("cost_price", encoded)
        self.assertNotIn(TOKEN, encoded)

    @override_settings(
        KASPI_OWN_MERCHANT_IDS="30363568",
        KASPI_REPRICER_UNDERCUT_AMOUNT=300,
    )
    def test_manifest_returns_own_merchant_ids(self):
        response = self.client.get(LISTINGS_URL, **self._auth())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["own_merchant_ids"], ["30363568"])
        self.assertIsInstance(payload["listings"], list)

    @override_settings(KASPI_REPRICER_UNDERCUT_AMOUNT=300)
    def test_manifest_returns_undercut_amount(self):
        response = self.client.get(LISTINGS_URL, **self._auth())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["undercut_amount"], 300)

    def test_manifest_ids_filter_does_not_substitute(self):
        other = _listing("OTHER", "222")
        response = self.client.get(
            LISTINGS_URL,
            {"ids": f"{self.listing.pk},999999"},
            **self._auth(),
        )
        ids = [row["listing_id"] for row in response.json()["listings"]]
        self.assertEqual(ids, [self.listing.pk])
        self.assertNotIn(other.pk, ids)

    def test_manifest_get_is_zero_write(self):
        before = (
            KaspiCompetitorOfferSnapshot.objects.count(),
            KaspiCompetitorIngestBatch.objects.count(),
            ProductKaspiListing.objects.count(),
        )
        response = self.client.get(LISTINGS_URL, **self._auth())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            (
                KaspiCompetitorOfferSnapshot.objects.count(),
                KaspiCompetitorIngestBatch.objects.count(),
                ProductKaspiListing.objects.count(),
            ),
            before,
        )

    def test_post_without_token_is_401(self):
        response = self.client.post(
            BATCHES_URL,
            data=json.dumps(_batch_payload(self.listing)),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(KaspiCompetitorOfferSnapshot.objects.count(), 0)

    def test_post_wrong_token_is_401(self):
        response = self.client.post(
            BATCHES_URL,
            data=json.dumps(_batch_payload(self.listing)),
            content_type="application/json",
            **self._auth("nope"),
        )
        self.assertEqual(response.status_code, 401)

    def test_valid_batch_creates_ingest_and_snapshots(self):
        payload = _batch_payload(self.listing)
        response = self._post(payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["duplicate"])
        self.assertEqual(body["snapshots_created"], 2)
        self.assertEqual(KaspiCompetitorIngestBatch.objects.count(), 1)
        self.assertEqual(KaspiCompetitorOfferSnapshot.objects.count(), 2)
        snapshot = KaspiCompetitorOfferSnapshot.objects.order_by("price").first()
        self.assertEqual(snapshot.source, "office_collector")
        self.assertEqual(snapshot.listing_id, self.listing.pk)
        self.assertEqual(snapshot.price, Decimal("3033.00"))

    def test_duplicate_batch_id_same_listing_does_not_duplicate_snapshots(self):
        payload = _batch_payload(self.listing)
        first = self._post(payload)
        second = self._post(payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(second.json()["snapshots_created"], 0)
        self.assertEqual(KaspiCompetitorOfferSnapshot.objects.count(), 2)
        self.assertEqual(KaspiCompetitorIngestBatch.objects.count(), 1)

    def test_duplicate_batch_id_other_listing_is_409(self):
        payload = _batch_payload(self.listing)
        self.assertEqual(self._post(payload).status_code, 200)
        other = _listing("OTHER", "222")
        payload["listing_id"] = other.pk
        payload["master_sku"] = other.master_sku
        response = self._post(payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(KaspiCompetitorOfferSnapshot.objects.count(), 2)
        self.assertEqual(KaspiCompetitorIngestBatch.objects.count(), 1)

    def test_master_sku_mismatch_rejects(self):
        payload = _batch_payload(self.listing, master_sku="WRONG")
        response = self._post(payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(KaspiCompetitorOfferSnapshot.objects.count(), 0)
        self.assertEqual(KaspiCompetitorIngestBatch.objects.count(), 0)

    def test_inactive_listing_rejects(self):
        self.listing.is_active = False
        self.listing.save(update_fields=["is_active"])
        response = self._post(_batch_payload(self.listing))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(KaspiCompetitorOfferSnapshot.objects.count(), 0)

    def test_malformed_json_rejects(self):
        response = self.client.post(
            BATCHES_URL,
            data="{",
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(KaspiCompetitorOfferSnapshot.objects.count(), 0)

    def test_more_than_100_offers_rejects(self):
        offers = [_offer_payload(position=index + 1, seller_code=str(index)) for index in range(101)]
        response = self._post(_batch_payload(self.listing, offers=offers))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(KaspiCompetitorOfferSnapshot.objects.count(), 0)

    def test_invalid_price_rejects(self):
        for price in ("0", "-1", "abc"):
            response = self._post(
                _batch_payload(self.listing, offers=[_offer_payload(price=price)])
            )
            self.assertEqual(response.status_code, 400, price)
        self.assertEqual(KaspiCompetitorOfferSnapshot.objects.count(), 0)

    def test_one_invalid_offer_rejects_whole_batch(self):
        offers = [_offer_payload(), _offer_payload(price="0", seller_code="BAD")]
        response = self._post(_batch_payload(self.listing, offers=offers))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(KaspiCompetitorOfferSnapshot.objects.count(), 0)
        self.assertEqual(KaspiCompetitorIngestBatch.objects.count(), 0)

    def test_ingest_does_not_create_repricer_or_stock_writes(self):
        listing_before = (
            self.listing.last_known_our_price,
            self.listing.last_known_kaspi_qty,
            self.listing.public_url,
        )
        product_price = self.listing.product.price
        response = self._post(_batch_payload(self.listing))
        self.assertEqual(response.status_code, 200)
        self.listing.refresh_from_db()
        self.listing.product.refresh_from_db()
        self.assertEqual(
            (
                self.listing.last_known_our_price,
                self.listing.last_known_kaspi_qty,
                self.listing.public_url,
            ),
            listing_before,
        )
        self.assertEqual(self.listing.product.price, product_price)
        self.assertEqual(KaspiRepricerRule.objects.count(), 0)
        self.assertEqual(KaspiRepricerRecommendation.objects.count(), 0)
        self.assertEqual(KaspiOwnPriceSnapshot.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)

    @override_settings(KASPI_COMPETITOR_COLLECTOR_TOKEN="")
    def test_missing_server_token_is_unavailable(self):
        response = self.client.get(LISTINGS_URL, **self._auth())
        self.assertEqual(response.status_code, 503)
