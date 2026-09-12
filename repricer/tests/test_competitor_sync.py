from decimal import Decimal

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from catalog.models import Product, ProductKaspiListing
from integrations.kaspi_competitors import (
    CompetitorPriceSourceRateLimited,
    KaspiCompetitorOffer,
    KaspiPublicOfferSource,
)
from repricer.competitor_sync import sync_competitor_offers_for_listing
from repricer.models import KaspiCompetitorOfferSnapshot
from repricer.services import RepricerConfigurationError, latest_competitor_prices


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class FakeOfferSource:
    def fetch_offers(self, *, master_sku, merchant_sku=""):
        return [
            KaspiCompetitorOffer(
                seller_name="Продавец 1",
                seller_code="SELLER-1",
                price=Decimal("12490"),
                position=1,
            ),
            KaspiCompetitorOffer(
                seller_name="Продавец 2",
                seller_code="SELLER-2",
                price=Decimal("13000"),
                position=2,
            ),
        ]


class KaspiPublicOfferSourceTests(SimpleTestCase):
    def test_normalizes_public_offers(self):
        session = FakeSession(
            FakeResponse(
                payload={
                    "offers": [
                        {"merchantName": "Shop A", "merchantId": "A1", "price": 12500},
                        {"merchantName": "Shop B", "merchantId": "B1", "price": "13 000"},
                        {"merchantName": "Bad", "merchantId": "BAD", "price": None},
                    ]
                }
            )
        )
        source = KaspiPublicOfferSource(session=session, city_id="750000000")

        offers = source.fetch_offers(master_sku="123456789")

        self.assertEqual(len(offers), 2)
        self.assertEqual(offers[0].seller_name, "Shop A")
        self.assertEqual(offers[0].seller_code, "A1")
        self.assertEqual(offers[0].price, Decimal("12500"))
        self.assertEqual(offers[0].position, 1)
        self.assertEqual(offers[1].price, Decimal("13000"))
        self.assertEqual(len(session.calls), 1)
        url, kwargs = session.calls[0]
        self.assertIn("/yml/offer-view/offers/123456789", url)
        self.assertEqual(kwargs["json"]["cityId"], "750000000")
        self.assertEqual(kwargs["timeout"], 10.0)

    def test_rate_limit_fails_closed(self):
        source = KaspiPublicOfferSource(
            session=FakeSession(FakeResponse(status_code=429, payload={}))
        )

        with self.assertRaises(CompetitorPriceSourceRateLimited):
            source.fetch_offers(master_sku="123456789")


class CompetitorSyncTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            title="Тестовый фильтр",
            article="TEST-KASPI-001",
            seller_name="AG Parts",
            whatsapp_number="+77000000000",
        )
        self.listing = ProductKaspiListing.objects.create(
            product=self.product,
            master_sku="123456789",
            merchant_sku="TEST-KASPI-001",
            last_known_our_price=12900,
            is_active=True,
        )

    def test_sync_appends_snapshots_only(self):
        result = sync_competitor_offers_for_listing(
            listing=self.listing,
            source=FakeOfferSource(),
            source_name="kaspi_public",
        )

        self.assertEqual(result.offers_received, 2)
        self.assertEqual(result.snapshots_created, 2)
        self.assertEqual(KaspiCompetitorOfferSnapshot.objects.count(), 2)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.last_known_our_price, 12900)

    @override_settings(
        KASPI_OWN_MERCHANT_IDS="",
        KASPI_OWN_MERCHANT_NAMES="",
    )
    def test_public_data_is_not_used_until_own_shop_is_identified(self):
        now = timezone.now()
        KaspiCompetitorOfferSnapshot.objects.create(
            listing=self.listing,
            seller_name="Our Shop",
            seller_code="OUR-1",
            price=Decimal("12900"),
            source="kaspi_public",
            captured_at=now,
        )
        KaspiCompetitorOfferSnapshot.objects.create(
            listing=self.listing,
            seller_name="Competitor",
            seller_code="OTHER-1",
            price=Decimal("12500"),
            source="kaspi_public",
            captured_at=now,
        )

        with self.assertRaises(RepricerConfigurationError):
            latest_competitor_prices(listing=self.listing)

    @override_settings(
        KASPI_OWN_MERCHANT_IDS="OUR-1",
        KASPI_OWN_MERCHANT_NAMES="Our Shop",
    )
    def test_own_shop_is_excluded_from_public_competitor_prices(self):
        now = timezone.now()
        KaspiCompetitorOfferSnapshot.objects.create(
            listing=self.listing,
            seller_name="Our Shop",
            seller_code="OUR-1",
            price=Decimal("12000"),
            source="kaspi_public",
            captured_at=now,
        )
        KaspiCompetitorOfferSnapshot.objects.create(
            listing=self.listing,
            seller_name="Competitor",
            seller_code="OTHER-1",
            price=Decimal("12500"),
            source="kaspi_public",
            captured_at=now,
        )

        prices, count, _ = latest_competitor_prices(listing=self.listing)

        self.assertEqual(count, 1)
        self.assertEqual(prices, [Decimal("12500.00")])
