"""Read-only Kaspi recommendation preview.

Formula: best current competitor price minus a global undercut amount.
This module never writes KaspiRepricerRecommendation, never changes
Product.price, and never writebacks to Kaspi.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings

from repricer.competitor_display import STATE_READY, ListingCompetitorState

DEFAULT_UNDERCUT_AMOUNT = Decimal("300")


@dataclass(frozen=True)
class RecommendationPreview:
    amount: Decimal | None
    undercut_amount: Decimal
    actionable: bool
    note: str
    state: str

    @property
    def label(self) -> str:
        if self.amount is None:
            return "—"
        quantized = self.amount.quantize(Decimal("1"))
        return f"{quantized:,.0f} ₸".replace(",", " ")

    @property
    def sublabel(self) -> str:
        if not self.actionable:
            return ""
        undercut = self.undercut_amount.quantize(Decimal("1"))
        return f"−{undercut:,.0f} ₸".replace(",", " ")


def undercut_amount() -> Decimal:
    raw = getattr(settings, "KASPI_REPRICER_UNDERCUT_AMOUNT", DEFAULT_UNDERCUT_AMOUNT)
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return DEFAULT_UNDERCUT_AMOUNT
    if value < 0:
        return DEFAULT_UNDERCUT_AMOUNT
    return value


def recommendation_preview(state: ListingCompetitorState) -> RecommendationPreview:
    """Return a display-only recommendation from the latest competitor state.

    READY: competitor_min - undercut, hidden when the result is not positive.
    STALE: competitor is shown separately as stale; recommendation is not
    actionable because the office collector has not refreshed the batch.
    All other collection states: no recommendation.
    """

    cut = undercut_amount()
    if state.state != STATE_READY or state.best_price is None:
        return RecommendationPreview(
            amount=None,
            undercut_amount=cut,
            actionable=False,
            note=_note_for(state.state),
            state=state.state,
        )
    recommended = state.best_price - cut
    if recommended <= 0:
        return RecommendationPreview(
            amount=None,
            undercut_amount=cut,
            actionable=False,
            note="Рекомендация неположительная",
            state=state.state,
        )
    return RecommendationPreview(
        amount=recommended,
        undercut_amount=cut,
        actionable=True,
        note=f"Конкурент {state.best_price} ₸ минус {cut} ₸",
        state=state.state,
    )


def _note_for(state: str) -> str:
    if state == "STALE":
        return "Снимок устарел — рекомендация не actionable"
    if state == "NO_OTHER_OFFERS":
        return "Нет чужих продавцов"
    if state == "UNRESOLVED_MAPPING":
        return "Нет надёжного числового Kaspi product id"
    if state == "NO_DATA":
        return "Нет снимка конкурентов"
    if state == "OWN_MERCHANT_NOT_CONFIGURED":
        return "Не настроен собственный продавец Kaspi"
    return "Нет рекомендации"
