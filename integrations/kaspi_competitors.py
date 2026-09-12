from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, Sequence


@dataclass(frozen=True)
class KaspiCompetitorOffer:
    """Normalized competitor offer consumed by the repricer.

    External source-specific payloads must be converted to this small contract
    before they reach pricing logic.  That keeps the repricer independent from
    a future official/partner feed or another approved provider.
    """

    seller_name: str
    price: Decimal
    seller_code: str = ""
    position: int | None = None
    is_available: bool = True


class KaspiCompetitorPriceSource(Protocol):
    def fetch_offers(
        self,
        *,
        master_sku: str,
        merchant_sku: str = "",
    ) -> Sequence[KaspiCompetitorOffer]:
        """Return normalized competing offers for one Kaspi product."""
        ...


class CompetitorPriceSourceNotConfigured(RuntimeError):
    pass


class NotConfiguredKaspiCompetitorPriceSource:
    """Fail-closed source used until a reliable competitor feed is approved.

    Repricer v1 must not silently scrape an undocumented endpoint or invent
    competitor prices.  Once a source is selected, implement the protocol above
    and keep the pricing engine unchanged.
    """

    def fetch_offers(
        self,
        *,
        master_sku: str,
        merchant_sku: str = "",
    ) -> Sequence[KaspiCompetitorOffer]:
        del merchant_sku
        raise CompetitorPriceSourceNotConfigured(
            f"Источник цен конкурентов Kaspi не настроен для master_sku={master_sku}."
        )
