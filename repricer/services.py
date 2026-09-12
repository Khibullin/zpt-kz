from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from catalog.kaspi_repricing import RepricingDecision, RepricingPolicy, recommend_kaspi_price
from catalog.models import ProductKaspiListing

from .models import (
    KaspiCompetitorOfferSnapshot,
    KaspiOwnPriceSnapshot,
    KaspiRepricerRecommendation,
    KaspiRepricerRule,
)


class RepricerConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RecommendationResult:
    recommendation: KaspiRepricerRecommendation
    competitor_count: int
    competitor_data_since: datetime


def get_current_kaspi_price(listing: ProductKaspiListing) -> Decimal:
    """Return the last price explicitly known from Kaspi data.

    Do not fall back to Product.price: ZPT.KZ and Kaspi are separate sales
    channels and may intentionally have different prices.
    """

    if listing.last_known_our_price is None:
        raise RepricerConfigurationError(
            "Для Kaspi-листинга нет last_known_our_price. "
            "Сначала синхронизируйте нашу цену Kaspi."
        )
    return Decimal(listing.last_known_our_price)


def latest_competitor_prices(
    *,
    listing: ProductKaspiListing,
    max_age_minutes: int = 60,
) -> tuple[list[Decimal], int, datetime]:
    """Return one latest fresh price per competitor.

    Deduplication is intentionally done in Python to stay portable between
    PostgreSQL in production and SQLite in local/test environments.
    """

    if max_age_minutes <= 0:
        raise ValueError("max_age_minutes must be positive")

    cutoff = timezone.now() - timedelta(minutes=max_age_minutes)
    snapshots = (
        KaspiCompetitorOfferSnapshot.objects.filter(
            listing=listing,
            is_available=True,
            captured_at__gte=cutoff,
        )
        .order_by("-captured_at", "price", "id")
    )

    seen: set[str] = set()
    prices: list[Decimal] = []
    for snapshot in snapshots:
        key = (snapshot.seller_code or snapshot.seller_name).strip().casefold()
        if not key:
            key = f"snapshot:{snapshot.pk}"
        if key in seen:
            continue
        seen.add(key)
        prices.append(snapshot.price)

    prices.sort()
    return prices, len(prices), cutoff


def _policy_from_rule(rule: KaspiRepricerRule) -> RepricingPolicy:
    return RepricingPolicy(
        min_price=rule.min_price,
        price_step=rule.price_step,
        max_change_percent=rule.max_change_percent,
        allow_raise=rule.allow_raise,
    )


def _persist_decision(
    *,
    rule: KaspiRepricerRule,
    decision: RepricingDecision,
) -> KaspiRepricerRecommendation:
    return KaspiRepricerRecommendation.objects.create(
        listing=rule.listing,
        rule=rule,
        current_price=decision.current_price,
        best_competitor_price=decision.best_competitor_price,
        recommended_price=decision.recommended_price,
        market_position=decision.market_position,
        action=decision.action,
        reason_code=decision.reason_code,
        reason=decision.reason,
    )


@transaction.atomic
def generate_recommendation(
    *,
    rule: KaspiRepricerRule,
    max_age_minutes: int = 60,
) -> RecommendationResult:
    """Generate and persist one recommendation; never writes to Kaspi."""

    if not rule.is_enabled:
        raise RepricerConfigurationError("Правило репрайсера отключено.")

    listing = ProductKaspiListing.objects.select_related("product").get(pk=rule.listing_id)
    current_price = get_current_kaspi_price(listing)
    competitor_prices, competitor_count, cutoff = latest_competitor_prices(
        listing=listing,
        max_age_minutes=max_age_minutes,
    )

    decision = recommend_kaspi_price(
        current_price=current_price,
        competitor_prices=competitor_prices,
        policy=_policy_from_rule(rule),
    )

    KaspiOwnPriceSnapshot.objects.create(
        listing=listing,
        price=decision.current_price,
        source="repricer",
        captured_at=timezone.now(),
    )
    recommendation = _persist_decision(rule=rule, decision=decision)

    return RecommendationResult(
        recommendation=recommendation,
        competitor_count=competitor_count,
        competitor_data_since=cutoff,
    )
