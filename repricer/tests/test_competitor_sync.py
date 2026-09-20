from decimal import Decimal

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from catalog.models import Product, ProductKaspiListing
from integrations.kaspi_competitors import (
    CompetitorPriceSourceError,
    CompetitorPriceSourceRateLimited,
    CompetitorPriceSourceUnavailable,
    KaspiCompetitorOffer,
    KaspiPublicOfferSource,
    kaspi_public_product_id,
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
    def fetch_offers(self, *, master_sku, merchant_sku="", public_url=""):
        del public_url
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
    def test_extracts_card_id_from_composite_export_sku(self):
        self.assertEqual(
            KaspiPublicOfferSource._product_id("116207063_792647100"),
            "116207063",
        )

    def test_plain_numeric_active_sku_is_unresolved_even_when_not_merchant(self):
        # ACTIVE.xlsx SKU column for these rows is digits-only and is not a
        # public Kaspi card id (same 404 / empty-offers signature as an
        # underscore suffix). Do not collect against it.
        self.assertIsNone(
            kaspi_public_product_id("126807700", merchant_sku="272774M400")
        )
        self.assertIsNone(
            kaspi_public_product_id("801748033", merchant_sku="301000265AA")
        )
        self.assertIsNone(
            kaspi_public_product_id("806873204", merchant_sku="4801012010")
        )
        self.assertIsNone(
            kaspi_public_product_id("123456789", merchant_sku="TEST-KASPI-001")
        )
        with self.assertRaises(CompetitorPriceSourceError):
            KaspiPublicOfferSource._product_id("126807700", merchant_sku="272774M400")

    def test_numeric_prefix_before_underscore_is_collectable(self):
        self.assertEqual(
            kaspi_public_product_id("115801437_271928151", merchant_sku="X0390000206"),
            "115801437",
        )
        self.assertEqual(
            kaspi_public_product_id(
                "135426201_722693725", merchant_sku="2032047000"
            ),
            "135426201",
        )
        self.assertEqual(
            KaspiPublicOfferSource._product_id("116207063_792647100", "116207063_792647100"),
            "116207063",
        )

    def test_plain_numeric_equal_to_merchant_sku_is_unresolved(self):
        self.assertIsNone(kaspi_public_product_id("8890649934", merchant_sku="8890649934"))
        self.assertIsNone(kaspi_public_product_id("8025530500", merchant_sku="8025530500"))
        self.assertIsNone(kaspi_public_product_id("1056025900", merchant_sku="1056025900"))
        with self.assertRaises(CompetitorPriceSourceError):
            KaspiPublicOfferSource._product_id("8890649934", merchant_sku="8890649934")

    def test_plain_numeric_active_sku_does_not_http(self):
        session = FakeSession(FakeResponse(payload={"offers": []}))
        source = KaspiPublicOfferSource(session=session)
        with self.assertRaises(CompetitorPriceSourceError):
            source.fetch_offers(master_sku="126807700", merchant_sku="272774M400")
        self.assertEqual(session.calls, [])

    def test_bound_public_url_makes_plain_active_sku_collectable(self):
        url = "https://kaspi.kz/shop/p/filtr-vozdushnyi-272774m400-987654321/"
        self.assertEqual(
            kaspi_public_product_id(
                "126807700",
                merchant_sku="272774M400",
                public_url=url,
            ),
            "987654321",
        )
        self.assertEqual(
            KaspiPublicOfferSource._product_id(
                "126807700",
                "272774M400",
                public_url=url,
            ),
            "987654321",
        )

    def test_public_url_agrees_with_underscore_sku(self):
        url = "https://kaspi.kz/shop/p/filtr-135426201/"
        self.assertEqual(
            kaspi_public_product_id(
                "135426201_722693725",
                merchant_sku="2032047000",
                public_url=url,
            ),
            "135426201",
        )
        self.assertEqual(
            KaspiPublicOfferSource._product_id(
                "135426201_722693725",
                "2032047000",
                public_url=url,
            ),
            "135426201",
        )

    def test_public_url_and_underscore_sku_conflict_fails_closed(self):
        url = "https://kaspi.kz/shop/p/salonnyi-fil-tr-272774m400-138029683/"
        self.assertIsNone(
            kaspi_public_product_id(
                "999888777_111",
                merchant_sku="272774M400",
                public_url=url,
            )
        )
        with self.assertRaises(CompetitorPriceSourceError) as ctx:
            KaspiPublicOfferSource._product_id(
                "999888777_111",
                "272774M400",
                public_url=url,
            )
        self.assertIn("Конфликт", str(ctx.exception))

    def test_three_cabin_oil_filter_public_url_bindings(self):
        cases = (
            (
                "272774M400",
                "126807700",
                "https://kaspi.kz/shop/p/salonnyi-fil-tr-272774m400-138029683/",
                "138029683",
            ),
            (
                "301000265AA",
                "801748033",
                "https://kaspi.kz/shop/p/salonnyi-fil-tr-301000265aa-141508690/",
                "141508690",
            ),
            (
                "4801012010",
                "806873204",
                "https://kaspi.kz/shop/p/masljanyi-fil-tr-4801012010-139279709/",
                "139279709",
            ),
        )
        for merchant_sku, master_sku, url, expected in cases:
            with self.subTest(article=merchant_sku):
                self.assertEqual(
                    kaspi_public_product_id(
                        master_sku,
                        merchant_sku=merchant_sku,
                        public_url=url,
                    ),
                    expected,
                )
                self.assertEqual(
                    KaspiPublicOfferSource._product_id(
                        master_sku,
                        merchant_sku,
                        public_url=url,
                    ),
                    expected,
                )

    def test_working_control_underscore_sku_without_public_url(self):
        self.assertEqual(
            kaspi_public_product_id(
                "135426201_722693725",
                merchant_sku="2032047000",
            ),
            "135426201",
        )

    def test_alphanumeric_master_sku_is_unresolved(self):
        self.assertIsNone(kaspi_public_product_id("1017110XEN01"))
        self.assertIsNone(kaspi_public_product_id("X01-90000014"))
        self.assertIsNone(kaspi_public_product_id("EM2E8121211E"))
        self.assertIsNone(kaspi_public_product_id("272774M400", merchant_sku="272774M400"))
        self.assertIsNone(kaspi_public_product_id("301000265AA", merchant_sku="301000265AA"))

    def test_bound_public_url_makes_oem_master_sku_collectable(self):
        url = "https://kaspi.kz/shop/p/filtr-vozdushnyi-272774m400-987654321/"
        self.assertEqual(
            kaspi_public_product_id(
                "272774M400",
                merchant_sku="272774M400",
                public_url=url,
            ),
            "987654321",
        )
        self.assertEqual(
            KaspiPublicOfferSource._product_id(
                "272774M400",
                "272774M400",
                public_url=url,
            ),
            "987654321",
        )

    def test_public_url_id_equal_to_merchant_sku_is_unresolved(self):
        url = "https://kaspi.kz/shop/p/filtr-maslianyi-4801012010/"
        self.assertIsNone(
            kaspi_public_product_id(
                "4801012010",
                merchant_sku="4801012010",
                public_url=url,
            )
        )

    def test_does_not_guess_from_merchant_sku(self):
        self.assertIsNone(kaspi_public_product_id("P8104140"))
        with self.assertRaises(CompetitorPriceSourceError):
            KaspiPublicOfferSource._product_id("P8104140")

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

        offers = source.fetch_offers(master_sku="123456789_555555555")

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
        session = FakeSession(FakeResponse(status_code=429, payload={}))
        source = KaspiPublicOfferSource(session=session)

        with self.assertRaises(CompetitorPriceSourceRateLimited) as caught:
            source.fetch_offers(master_sku="123456789_555555555")
        self.assertEqual(caught.exception.http_status, 429)
        self.assertEqual(len(session.calls), 1)

    def test_http_405_fails_closed(self):
        session = FakeSession(FakeResponse(status_code=405, payload={}))
        source = KaspiPublicOfferSource(session=session)

        with self.assertRaises(CompetitorPriceSourceUnavailable) as caught:
            source.fetch_offers(master_sku="123456789_555555555")
        self.assertEqual(caught.exception.http_status, 405)
        self.assertEqual(len(session.calls), 1)

    def test_http_403_fails_closed_without_retry(self):
        session = FakeSession(FakeResponse(status_code=403, payload={}))
        source = KaspiPublicOfferSource(session=session)

        with self.assertRaises(CompetitorPriceSourceUnavailable) as caught:
            source.fetch_offers(master_sku="123456789_555555555")
        self.assertEqual(caught.exception.http_status, 403)
        self.assertEqual(len(session.calls), 1)


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
            master_sku="123456789_555555555",
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
