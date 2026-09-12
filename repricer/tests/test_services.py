from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from catalog.models import Product, ProductKaspiListing
from repricer.models import (
    KaspiCompetitorOfferSnapshot,
    KaspiOwnPriceSnapshot,
    KaspiRepricerRecommendation,
    KaspiRepricerRule,
)
from repricer.services import (
    RepricerConfigurationError,
    generate_recommendation,
    latest_competitor_prices,
)


class KaspiRepricerServiceTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            title="Тестовый фильтр",
            article="TEST-FILTER-001",
            seller_name="AG Parts",
            whatsapp_number="+77000000000",
        )
        self.listing = ProductKaspiListing.objects.create(
            product=self.product,
            master_sku="KASPI-MASTER-001",
            merchant_sku="TEST-FILTER-001",
            last_known_our_price=12900,
            is_active=True,
        )
        self.rule = KaspiRepricerRule.objects.create(
            listing=self.listing,
            min_price=Decimal("10800"),
            price_step=Decimal("10"),
            max_change_percent=Decimal("20"),
        )

    def _offer(self, seller, price, *, minutes_ago=0, seller_code=""):
        return KaspiCompetitorOfferSnapshot.objects.create(
            listing=self.listing,
            seller_name=seller,
            seller_code=seller_code,
            price=Decimal(price),
            captured_at=timezone.now() - timedelta(minutes=minutes_ago),
        )

    def test_generates_and_persists_lower_recommendation(self):
        self._offer("Конкурент 1", "12500")
        self._offer("Конкурент 2", "13200")

        result = generate_recommendation(rule=self.rule, max_age_minutes=60)

        recommendation = result.recommendation
        self.assertEqual(result.competitor_count, 2)
        self.assertEqual(recommendation.action, "LOWER")
        self.assertEqual(recommendation.current_price, Decimal("12900.00"))
        self.assertEqual(recommendation.best_competitor_price, Decimal("12500.00"))
        self.assertEqual(recommendation.recommended_price, Decimal("12490.00"))
        self.assertEqual(KaspiRepricerRecommendation.objects.count(), 1)
        self.assertEqual(KaspiOwnPriceSnapshot.objects.count(), 1)

    def test_stale_competitor_data_is_ignored_and_price_is_held(self):
        self._offer("Старый конкурент", "9000", minutes_ago=120)

        result = generate_recommendation(rule=self.rule, max_age_minutes=60)

        self.assertEqual(result.competitor_count, 0)
        self.assertEqual(result.recommendation.action, "HOLD")
        self.assertEqual(result.recommendation.reason_code, "NO_COMPETITOR_DATA")
        self.assertEqual(result.recommendation.recommended_price, Decimal("12900.00"))

    def test_latest_price_per_seller_is_used(self):
        self._offer("Конкурент", "12000", minutes_ago=10, seller_code="SELLER-1")
        self._offer("Конкурент", "12500", minutes_ago=1, seller_code="SELLER-1")
        self._offer("Другой", "13000", minutes_ago=1, seller_code="SELLER-2")

        prices, count, _ = latest_competitor_prices(
            listing=self.listing,
            max_age_minutes=60,
        )

        self.assertEqual(count, 2)
        self.assertEqual(prices, [Decimal("12500.00"), Decimal("13000.00")])

    def test_missing_kaspi_price_fails_closed(self):
        self.listing.last_known_our_price = None
        self.listing.save(update_fields=["last_known_our_price"])

        with self.assertRaises(RepricerConfigurationError):
            generate_recommendation(rule=self.rule, max_age_minutes=60)

        self.assertEqual(KaspiRepricerRecommendation.objects.count(), 0)
        self.assertEqual(KaspiOwnPriceSnapshot.objects.count(), 0)

    def test_disabled_rule_is_not_processed(self):
        self.rule.is_enabled = False
        self.rule.save(update_fields=["is_enabled"])

        with self.assertRaises(RepricerConfigurationError):
            generate_recommendation(rule=self.rule, max_age_minutes=60)
