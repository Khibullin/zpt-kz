from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from catalog.models import Product, ProductKaspiListing, StockMovement
from repricer.competitor_display import (
    STATE_NO_OTHER_OFFERS,
    STATE_READY,
    STATE_STALE,
    STATE_UNRESOLVED_MAPPING,
    listing_competitor_state,
)
from repricer.models import (
    KaspiCompetitorOfferSnapshot,
    KaspiRepricerRecommendation,
    KaspiRepricerRule,
)
from repricer.recommendation_preview import recommendation_preview


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
        last_known_our_price=3034,
        is_active=True,
    )


def _offer(listing, *, seller_name, price, seller_code="", captured_at=None, **kwargs):
    defaults = {
        "listing": listing,
        "seller_name": seller_name,
        "seller_code": seller_code,
        "price": Decimal(str(price)),
        "is_available": True,
        "source": "office_collector",
        "captured_at": captured_at or timezone.now(),
    }
    defaults.update(kwargs)
    return KaspiCompetitorOfferSnapshot.objects.create(**defaults)


class RecommendationPreviewTests(TestCase):
    @override_settings(
        KASPI_OWN_MERCHANT_IDS=OWN_CODE,
        KASPI_OWN_MERCHANT_NAMES=OWN_NAME,
        KASPI_REPRICER_UNDERCUT_AMOUNT=300,
    )
    def test_ready_recommendation_is_competitor_minus_undercut(self):
        listing = _listing("REC-READY")
        now = timezone.now()
        _offer(listing, seller_name=OWN_NAME, seller_code=OWN_CODE, price="3034", captured_at=now)
        _offer(listing, seller_name="ИП ХАЛИБАЕВА", seller_code="30308762", price="3033", captured_at=now)
        _offer(listing, seller_name="A SHOP", seller_code="30249309", price="3084", captured_at=now)
        state = listing_competitor_state(listing.pk, now=now)
        self.assertEqual(state.state, STATE_READY)
        self.assertEqual(state.best_price, Decimal("3033"))
        preview = recommendation_preview(state)
        self.assertTrue(preview.actionable)
        self.assertEqual(preview.amount, Decimal("2733"))
        self.assertEqual(KaspiRepricerRecommendation.objects.count(), 0)
        self.assertEqual(KaspiRepricerRule.objects.count(), 0)
        listing.refresh_from_db()
        listing.product.refresh_from_db()
        self.assertEqual(listing.last_known_our_price, 3034)
        self.assertIsNone(listing.product.price)
        self.assertEqual(StockMovement.objects.count(), 0)

    @override_settings(KASPI_OWN_MERCHANT_IDS=OWN_CODE, KASPI_REPRICER_UNDERCUT_AMOUNT=300)
    def test_no_other_offers_has_no_recommendation(self):
        listing = _listing("REC-OWN")
        now = timezone.now()
        _offer(listing, seller_name=OWN_NAME, seller_code=OWN_CODE, price="3410", captured_at=now)
        state = listing_competitor_state(listing.pk, now=now)
        self.assertEqual(state.state, STATE_NO_OTHER_OFFERS)
        preview = recommendation_preview(state)
        self.assertFalse(preview.actionable)
        self.assertIsNone(preview.amount)

    def test_unresolved_mapping_has_no_recommendation(self):
        listing = _listing("REC-UNRES", master_sku="X01-90000014")
        state = listing_competitor_state(listing.pk)
        self.assertEqual(state.state, STATE_UNRESOLVED_MAPPING)
        preview = recommendation_preview(state)
        self.assertFalse(preview.actionable)
        self.assertIsNone(preview.amount)

    @override_settings(
        KASPI_OWN_MERCHANT_IDS=OWN_CODE,
        KASPI_COMPETITOR_FRESH_MINUTES=180,
        KASPI_REPRICER_UNDERCUT_AMOUNT=300,
    )
    def test_stale_is_not_actionable(self):
        listing = _listing("REC-STALE")
        now = timezone.now()
        _offer(
            listing,
            seller_name="Other",
            seller_code="30327411",
            price="1954",
            captured_at=now - timedelta(minutes=200),
        )
        state = listing_competitor_state(listing.pk, now=now)
        self.assertEqual(state.state, STATE_STALE)
        self.assertEqual(state.best_price, Decimal("1954"))
        preview = recommendation_preview(state)
        self.assertFalse(preview.actionable)
        self.assertIsNone(preview.amount)

    @override_settings(KASPI_OWN_MERCHANT_IDS=OWN_CODE, KASPI_REPRICER_UNDERCUT_AMOUNT=300)
    def test_listing_3_uses_foreign_min_not_own(self):
        listing = _listing("REC-L3", master_sku="129914457")
        now = timezone.now()
        _offer(listing, seller_name=OWN_NAME, seller_code=OWN_CODE, price="3740", captured_at=now)
        _offer(listing, seller_name="AMIOSPHY GROUP", seller_code="30440420", price="6864", captured_at=now)
        state = listing_competitor_state(listing.pk, now=now)
        self.assertEqual(state.state, STATE_READY)
        self.assertEqual(state.best_price, Decimal("6864"))
        preview = recommendation_preview(state)
        self.assertTrue(preview.actionable)
        self.assertEqual(preview.amount, Decimal("6564"))
        listing.refresh_from_db()
        self.assertEqual(listing.last_known_our_price, 3034)

    @override_settings(KASPI_OWN_MERCHANT_IDS=OWN_CODE, KASPI_REPRICER_UNDERCUT_AMOUNT=300)
    def test_listing_7_own_cheapest_still_uses_foreign_min(self):
        listing = _listing("REC-L7", master_sku="116207063")
        listing.last_known_our_price = 1150
        listing.save(update_fields=["last_known_our_price"])
        now = timezone.now()
        _offer(listing, seller_name=OWN_NAME, seller_code=OWN_CODE, price="1150", captured_at=now)
        _offer(listing, seller_name="Other", seller_code="30327411", price="1954", captured_at=now)
        state = listing_competitor_state(listing.pk, now=now)
        self.assertEqual(state.state, STATE_READY)
        self.assertEqual(state.best_price, Decimal("1954"))
        preview = recommendation_preview(state)
        self.assertEqual(preview.amount, Decimal("1654"))
        listing.refresh_from_db()
        self.assertEqual(listing.last_known_our_price, 1150)
        self.assertEqual(KaspiRepricerRecommendation.objects.count(), 0)

    @override_settings(KASPI_OWN_MERCHANT_IDS=OWN_CODE, KASPI_REPRICER_UNDERCUT_AMOUNT=5000)
    def test_non_positive_recommendation_is_hidden(self):
        listing = _listing("REC-LOW")
        now = timezone.now()
        _offer(listing, seller_name="Other", seller_code="1", price="100", captured_at=now)
        preview = recommendation_preview(listing_competitor_state(listing.pk, now=now))
        self.assertFalse(preview.actionable)
        self.assertIsNone(preview.amount)
