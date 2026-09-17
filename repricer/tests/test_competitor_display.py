from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from catalog.models import Product, ProductKaspiListing
from repricer.competitor_display import (
    STATE_NO_DATA,
    STATE_NO_OTHER_OFFERS,
    STATE_OWN_MERCHANT_NOT_CONFIGURED,
    STATE_READY,
    STATE_STALE,
    STATE_UNRESOLVED_MAPPING,
    competitor_states_for_listings,
    listing_competitor_state,
)
from repricer.models import KaspiCompetitorOfferSnapshot


OWN_CODE = "TEST-OWN"
OWN_NAME = "TEST-MERCHANT"


def _listing(article, master_sku="1001"):
    product = Product.objects.create(
        title=f"Title {article}",
        article=article,
        seller_name="Demo Seller",
        whatsapp_number="+77000000000",
    )
    return ProductKaspiListing.objects.create(
        product=product,
        master_sku=master_sku,
        merchant_sku=article,
        last_known_our_price=3000,
        is_active=True,
    )


def _offer(listing, *, seller_name, price, seller_code="", captured_at=None, **kwargs):
    defaults = {
        "listing": listing,
        "seller_name": seller_name,
        "seller_code": seller_code,
        "price": Decimal(str(price)),
        "is_available": True,
        "source": "kaspi_public",
        "captured_at": captured_at or timezone.now(),
    }
    defaults.update(kwargs)
    return KaspiCompetitorOfferSnapshot.objects.create(**defaults)


class CompetitorDisplayTests(TestCase):
    def test_no_snapshots_is_no_data(self):
        listing = _listing("NO-DATA")
        state = listing_competitor_state(listing.pk)
        self.assertEqual(state.state, STATE_NO_DATA)
        self.assertFalse(state.has_snapshot)
        self.assertIsNone(state.best_price)

    @override_settings(KASPI_OWN_MERCHANT_IDS=OWN_CODE, KASPI_OWN_MERCHANT_NAMES="")
    def test_own_seller_code_is_excluded_via_settings(self):
        listing = _listing("OWN-CODE")
        now = timezone.now()
        _offer(listing, seller_name=OWN_NAME, seller_code=OWN_CODE, price="3034", captured_at=now)
        _offer(listing, seller_name="Other", seller_code="30308762", price="3033", captured_at=now)

        state = listing_competitor_state(listing.pk, now=now)

        self.assertEqual(state.state, STATE_READY)
        self.assertEqual(state.best_price, Decimal("3033"))
        self.assertEqual(state.best_seller_code, "30308762")
        self.assertNotEqual(state.best_seller_code, OWN_CODE)

    @override_settings(KASPI_OWN_MERCHANT_IDS="", KASPI_OWN_MERCHANT_NAMES=OWN_NAME)
    def test_name_only_config_does_not_exclude_own_seller(self):
        listing = _listing("OWN-NAME")
        now = timezone.now()
        _offer(listing, seller_name="test-merchant", seller_code="other-code", price="3410", captured_at=now)
        _offer(listing, seller_name="AMIOSPHY GROUP", seller_code="30440420", price="6864", captured_at=now)

        state = listing_competitor_state(listing.pk, now=now)

        self.assertEqual(state.state, STATE_OWN_MERCHANT_NOT_CONFIGURED)
        self.assertIsNone(state.best_price)

    @override_settings(KASPI_OWN_MERCHANT_IDS="30363568", KASPI_OWN_MERCHANT_NAMES="")
    def test_ag_parts_merchant_id_is_excluded(self):
        listing = _listing("AG-PARTS")
        now = timezone.now()
        _offer(
            listing,
            seller_name="AG Parts",
            seller_code="30363568",
            price="1150",
            captured_at=now,
        )
        _offer(
            listing,
            seller_name="Other",
            seller_code="30327411",
            price="1954",
            captured_at=now,
        )

        state = listing_competitor_state(listing.pk, now=now)

        self.assertEqual(state.state, STATE_READY)
        self.assertEqual(state.best_price, Decimal("1954"))
        self.assertEqual(state.best_seller_code, "30327411")
        self.assertNotEqual(state.best_seller_code, "30363568")

    @override_settings(KASPI_OWN_MERCHANT_IDS=OWN_CODE, KASPI_OWN_MERCHANT_NAMES=OWN_NAME)
    def test_own_offer_never_becomes_best_competitor(self):
        listing = _listing("OWN-CHEAP")
        now = timezone.now()
        _offer(listing, seller_name=OWN_NAME, seller_code=OWN_CODE, price="1150", captured_at=now)
        _offer(listing, seller_name="Other", seller_code="30327411", price="1954", captured_at=now)

        state = listing_competitor_state(listing.pk, now=now)

        self.assertEqual(state.best_price, Decimal("1954"))
        self.assertNotEqual(state.best_seller_code, OWN_CODE)
        self.assertNotEqual(state.best_seller_name.casefold(), OWN_NAME.casefold())

    @override_settings(KASPI_OWN_MERCHANT_IDS=OWN_CODE, KASPI_OWN_MERCHANT_NAMES=OWN_NAME)
    def test_only_own_offer_is_no_other_offers(self):
        listing = _listing("ONLY-OWN")
        _offer(listing, seller_name=OWN_NAME, seller_code=OWN_CODE, price="3410")

        state = listing_competitor_state(listing.pk)

        self.assertEqual(state.state, STATE_NO_OTHER_OFFERS)
        self.assertTrue(state.has_snapshot)
        self.assertIsNone(state.best_price)
        self.assertEqual(state.competitor_count, 0)

    @override_settings(KASPI_OWN_MERCHANT_IDS="", KASPI_OWN_MERCHANT_NAMES="")
    def test_public_snapshots_are_ignored_without_own_merchant(self):
        listing = _listing("NO-OWN-CFG")
        now = timezone.now()
        _offer(listing, seller_name=OWN_NAME, seller_code=OWN_CODE, price="3034", captured_at=now)
        _offer(listing, seller_name="Other", seller_code="30308762", price="3033", captured_at=now)

        state = listing_competitor_state(listing.pk, now=now)

        self.assertEqual(state.state, STATE_OWN_MERCHANT_NOT_CONFIGURED)
        self.assertIsNone(state.best_price)
        self.assertEqual(state.competitor_count, 0)

    @override_settings(KASPI_OWN_MERCHANT_IDS=OWN_CODE)
    def test_uses_latest_batch_not_historical_minimum(self):
        listing = _listing("LATEST-ONLY")
        yesterday = timezone.now() - timedelta(days=1)
        today = timezone.now()
        _offer(listing, seller_name="Old", seller_code="OLD", price="1000", captured_at=yesterday)
        _offer(listing, seller_name="New", seller_code="NEW", price="2000", captured_at=today)

        state = listing_competitor_state(listing.pk, now=today)

        self.assertEqual(state.best_price, Decimal("2000"))
        self.assertEqual(state.best_seller_code, "NEW")
        self.assertNotEqual(state.best_price, Decimal("1000"))

    @override_settings(KASPI_OWN_MERCHANT_IDS=OWN_CODE)
    def test_minimum_is_inside_latest_capture_only(self):
        listing = _listing("BATCH-MIN")
        old = timezone.now() - timedelta(hours=4)
        latest = timezone.now()
        _offer(listing, seller_name="Yesterday cheap", seller_code="Y", price="1000", captured_at=old)
        _offer(listing, seller_name="Now A", seller_code="A", price="2500", captured_at=latest)
        _offer(listing, seller_name="Now B", seller_code="B", price="2000", captured_at=latest)

        state = listing_competitor_state(listing.pk, now=latest)

        self.assertEqual(state.best_price, Decimal("2000"))
        self.assertEqual(state.best_seller_code, "B")
        self.assertEqual(state.competitor_count, 2)

    @override_settings(
        KASPI_OWN_MERCHANT_IDS=OWN_CODE,
        KASPI_COMPETITOR_FRESH_MINUTES=180,
    )
    def test_stale_state_keeps_price(self):
        listing = _listing("STALE")
        captured = timezone.now() - timedelta(minutes=181)
        _offer(listing, seller_name="Other", seller_code="OTHER", price="3033", captured_at=captured)

        state = listing_competitor_state(listing.pk)

        self.assertEqual(state.state, STATE_STALE)
        self.assertTrue(state.is_stale)
        self.assertFalse(state.is_fresh)
        self.assertEqual(state.best_price, Decimal("3033"))

    @override_settings(KASPI_OWN_MERCHANT_IDS=OWN_CODE)
    def test_multiple_listings_are_isolated(self):
        first = _listing("ISO-A", master_sku="111")
        second = _listing("ISO-B", master_sku="222")
        now = timezone.now()
        _offer(first, seller_name="Cheap", seller_code="C1", price="1954", captured_at=now)
        _offer(second, seller_name="Dear", seller_code="C2", price="6864", captured_at=now)

        states = competitor_states_for_listings([first.pk, second.pk], now=now)

        self.assertEqual(states[first.pk].best_price, Decimal("1954"))
        self.assertEqual(states[second.pk].best_price, Decimal("6864"))
        self.assertEqual(states[first.pk].best_seller_code, "C1")
        self.assertEqual(states[second.pk].best_seller_code, "C2")

    @override_settings(KASPI_OWN_MERCHANT_IDS=OWN_CODE)
    def test_batch_lookup_uses_bounded_queries(self):
        now = timezone.now()
        ids = []
        for index in range(20):
            listing = _listing(f"Q-{index:02d}", master_sku=str(8000 + index))
            ids.append(listing.pk)
            _offer(
                listing,
                seller_name="Other",
                seller_code=f"S-{index}",
                price=str(2000 + index),
                captured_at=now,
            )
            _offer(
                listing,
                seller_name="Older",
                seller_code=f"O-{index}",
                price="1000",
                captured_at=now - timedelta(days=1),
            )

        with CaptureQueriesContext(connection) as captured:
            states = competitor_states_for_listings(ids, now=now)

        self.assertEqual(len(states), 20)
        self.assertEqual(states[ids[0]].best_price, Decimal("2000"))
        self.assertLessEqual(len(captured), 2)
        snapshot_sql = [
            query["sql"]
            for query in captured.captured_queries
            if "kaspicompetitoroffersnapshot" in query["sql"].lower()
        ]
        self.assertLessEqual(len(snapshot_sql), 2)

    @override_settings(KASPI_OWN_MERCHANT_IDS=OWN_CODE)
    def test_office_collector_source_excludes_own_seller(self):
        listing = _listing("COLLECTOR")
        now = timezone.now()
        _offer(
            listing,
            seller_name=OWN_NAME,
            seller_code=OWN_CODE,
            price="3034",
            captured_at=now,
            source="office_collector",
        )
        _offer(
            listing,
            seller_name="Other",
            seller_code="30308762",
            price="3033",
            captured_at=now,
            source="office_collector",
        )

        state = listing_competitor_state(listing.pk, now=now)

        self.assertEqual(state.state, STATE_READY)
        self.assertEqual(state.best_price, Decimal("3033"))
        self.assertEqual(state.best_seller_code, "30308762")


class UnresolvedMappingDisplayTests(TestCase):
    def test_alphanumeric_master_sku_is_unresolved_mapping(self):
        listing = _listing("UNRES", master_sku="1017110XEN01")
        state = listing_competitor_state(listing.pk)
        self.assertEqual(state.state, STATE_UNRESOLVED_MAPPING)
        self.assertIsNone(state.best_price)

    def test_numeric_master_equal_merchant_is_unresolved_mapping(self):
        listing = _listing("8890649934", master_sku="8890649934")
        state = listing_competitor_state(listing.pk)
        self.assertEqual(state.state, STATE_UNRESOLVED_MAPPING)
        self.assertIsNone(state.best_price)
