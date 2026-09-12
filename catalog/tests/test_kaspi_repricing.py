from decimal import Decimal

from django.test import SimpleTestCase

from catalog.kaspi_repricing import RepricingPolicy, recommend_kaspi_price


class KaspiRepricingTests(SimpleTestCase):
    def test_recommends_one_step_below_best_competitor(self):
        decision = recommend_kaspi_price(
            current_price=Decimal("12900"),
            competitor_prices=[Decimal("12500"), Decimal("13200")],
            policy=RepricingPolicy(
                min_price=Decimal("10800"),
                price_step=Decimal("10"),
                max_change_percent=Decimal("20"),
            ),
        )

        self.assertEqual(decision.action, "LOWER")
        self.assertEqual(decision.best_competitor_price, Decimal("12500.00"))
        self.assertEqual(decision.recommended_price, Decimal("12490.00"))
        self.assertEqual(decision.reason_code, "BEAT_BEST_PRICE")

    def test_never_recommends_below_min_price(self):
        decision = recommend_kaspi_price(
            current_price=Decimal("10500"),
            competitor_prices=[Decimal("9000")],
            policy=RepricingPolicy(
                min_price=Decimal("10000"),
                price_step=Decimal("10"),
                max_change_percent=Decimal("20"),
            ),
        )

        self.assertEqual(decision.action, "LOWER")
        self.assertEqual(decision.recommended_price, Decimal("10000.00"))
        self.assertEqual(decision.reason_code, "COMPETITOR_BELOW_FLOOR")

    def test_can_raise_price_when_market_moves_up(self):
        decision = recommend_kaspi_price(
            current_price=Decimal("6500"),
            competitor_prices=[Decimal("7100"), Decimal("7300")],
            policy=RepricingPolicy(
                min_price=Decimal("5600"),
                price_step=Decimal("10"),
                max_change_percent=Decimal("20"),
            ),
        )

        self.assertEqual(decision.action, "RAISE")
        self.assertEqual(decision.recommended_price, Decimal("7090.00"))
        self.assertEqual(decision.reason_code, "MARKET_MOVED_UP")
        self.assertEqual(decision.market_position, 1)

    def test_holds_when_competitor_data_is_missing(self):
        decision = recommend_kaspi_price(
            current_price=Decimal("12000"),
            competitor_prices=[],
            policy=RepricingPolicy(min_price=Decimal("10000")),
        )

        self.assertEqual(decision.action, "HOLD")
        self.assertEqual(decision.recommended_price, Decimal("12000.00"))
        self.assertEqual(decision.reason_code, "NO_COMPETITOR_DATA")

    def test_caps_large_price_change(self):
        decision = recommend_kaspi_price(
            current_price=Decimal("10000"),
            competitor_prices=[Decimal("5000")],
            policy=RepricingPolicy(
                min_price=Decimal("4000"),
                price_step=Decimal("10"),
                max_change_percent=Decimal("10"),
            ),
        )

        self.assertEqual(decision.action, "LOWER")
        self.assertEqual(decision.recommended_price, Decimal("9000.00"))

    def test_can_disable_price_increases(self):
        decision = recommend_kaspi_price(
            current_price=Decimal("6500"),
            competitor_prices=[Decimal("7100")],
            policy=RepricingPolicy(
                min_price=Decimal("5600"),
                price_step=Decimal("10"),
                max_change_percent=Decimal("20"),
                allow_raise=False,
            ),
        )

        self.assertEqual(decision.action, "HOLD")
        self.assertEqual(decision.recommended_price, Decimal("6500.00"))
        self.assertEqual(decision.reason_code, "RAISE_DISABLED")

    def test_market_position_counts_only_strictly_cheaper_offers(self):
        decision = recommend_kaspi_price(
            current_price=Decimal("10000"),
            competitor_prices=[Decimal("9500"), Decimal("10000"), Decimal("11000")],
            policy=RepricingPolicy(
                min_price=Decimal("8000"),
                price_step=Decimal("10"),
                max_change_percent=Decimal("20"),
            ),
        )

        self.assertEqual(decision.market_position, 2)
