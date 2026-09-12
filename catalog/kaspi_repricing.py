from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable


MONEY_QUANT = Decimal("0.01")
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class RepricingPolicy:
    """Safety limits for one Kaspi listing.

    The first version is intentionally recommendation-only.  This policy never
    sends a price to Kaspi; it only defines the bounds used by the decision
    engine.
    """

    min_price: Decimal
    price_step: Decimal = Decimal("1.00")
    max_change_percent: Decimal = Decimal("10.00")
    allow_raise: bool = True

    def __post_init__(self) -> None:
        if self.min_price < 0:
            raise ValueError("min_price must be non-negative")
        if self.price_step <= 0:
            raise ValueError("price_step must be positive")
        if self.max_change_percent <= 0:
            raise ValueError("max_change_percent must be positive")


@dataclass(frozen=True)
class RepricingDecision:
    action: str
    current_price: Decimal
    recommended_price: Decimal
    best_competitor_price: Decimal | None
    market_position: int | None
    reason_code: str
    reason: str

    @property
    def changed(self) -> bool:
        return self.recommended_price != self.current_price


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _valid_competitor_prices(prices: Iterable[Decimal]) -> list[Decimal]:
    return sorted(_money(price) for price in prices if price is not None and price > 0)


def _cap_change(
    *,
    current_price: Decimal,
    target_price: Decimal,
    max_change_percent: Decimal,
) -> Decimal:
    max_delta = current_price * max_change_percent / HUNDRED
    lower = current_price - max_delta
    upper = current_price + max_delta
    return min(max(target_price, lower), upper)


def _market_position(current_price: Decimal, competitor_prices: list[Decimal]) -> int:
    """Return price position where 1 means no competitor is cheaper.

    Ties are treated as the same position; only strictly cheaper offers count
    ahead of us.
    """

    return 1 + sum(1 for price in competitor_prices if price < current_price)


def recommend_kaspi_price(
    *,
    current_price: Decimal,
    competitor_prices: Iterable[Decimal],
    policy: RepricingPolicy,
) -> RepricingDecision:
    """Calculate a safe recommendation without changing any external system.

    Strategy for v1:
    - never recommend below ``min_price``;
    - target one ``price_step`` below the cheapest competitor when possible;
    - if the market moved up, the same target can raise our price;
    - cap every recommendation by ``max_change_percent`` per calculation;
    - with no competitor data, hold the current price rather than guessing.
    """

    current = _money(current_price)
    floor = _money(policy.min_price)
    step = _money(policy.price_step)

    if current <= 0:
        raise ValueError("current_price must be positive")

    prices = _valid_competitor_prices(competitor_prices)
    if not prices:
        safe_current = max(current, floor)
        return RepricingDecision(
            action="HOLD" if safe_current == current else "RAISE",
            current_price=current,
            recommended_price=safe_current,
            best_competitor_price=None,
            market_position=None,
            reason_code="NO_COMPETITOR_DATA" if safe_current == current else "BELOW_MIN_PRICE",
            reason=(
                "Нет актуальных цен конкурентов — цену не меняем."
                if safe_current == current
                else "Текущая цена ниже минимально допустимой — рекомендуем поднять до MIN."
            ),
        )

    best = prices[0]
    position = _market_position(current, prices)
    market_target = _money(best - step)
    target = max(floor, market_target)

    if target > current and not policy.allow_raise:
        return RepricingDecision(
            action="HOLD",
            current_price=current,
            recommended_price=current,
            best_competitor_price=best,
            market_position=position,
            reason_code="RAISE_DISABLED",
            reason="Рынок позволяет поднять цену, но повышение отключено политикой.",
        )

    capped = _money(
        _cap_change(
            current_price=current,
            target_price=target,
            max_change_percent=policy.max_change_percent,
        )
    )
    recommended = max(floor, capped)

    if recommended < current:
        action = "LOWER"
    elif recommended > current:
        action = "RAISE"
    else:
        action = "HOLD"

    if best <= floor:
        reason_code = "COMPETITOR_BELOW_FLOOR"
        reason = "Конкурент у MIN или ниже — ниже минимальной цены не идём."
    elif recommended != target:
        reason_code = "CHANGE_LIMITED"
        reason = "Целевая цена ограничена максимальным изменением за один расчёт."
    elif action == "LOWER":
        reason_code = "BEAT_BEST_PRICE"
        reason = "Можно безопасно снизиться на шаг ниже лучшего конкурента."
    elif action == "RAISE":
        reason_code = "MARKET_MOVED_UP"
        reason = "Рынок выше нашей цены — можно поднять цену, сохранив преимущество."
    else:
        reason_code = "PRICE_OPTIMAL"
        reason = "Текущая цена уже соответствует безопасной рыночной цели."

    return RepricingDecision(
        action=action,
        current_price=current,
        recommended_price=_money(recommended),
        best_competitor_price=best,
        market_position=position,
        reason_code=reason_code,
        reason=reason,
    )
