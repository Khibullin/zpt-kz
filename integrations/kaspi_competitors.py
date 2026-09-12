from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol, Sequence

import requests


KASPI_BASE_URL = "https://kaspi.kz"
DEFAULT_CITY_ID = "750000000"  # Алматы
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_OFFERS = 32


@dataclass(frozen=True)
class KaspiCompetitorOffer:
    """Normalized offer consumed by the repricer.

    External source-specific payloads are converted to this small contract
    before they reach pricing logic.  This keeps the repricer independent from
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


class CompetitorPriceSourceError(RuntimeError):
    """Base exception for a read-only competitor price source."""


class CompetitorPriceSourceNotConfigured(CompetitorPriceSourceError):
    pass


class CompetitorPriceSourceRateLimited(CompetitorPriceSourceError):
    pass


class CompetitorPriceSourceUnavailable(CompetitorPriceSourceError):
    pass


class NotConfiguredKaspiCompetitorPriceSource:
    """Fail-closed source used when no external feed is configured."""

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


class KaspiPublicOfferSource:
    """Read public Kaspi offer data for one product without changing anything.

    This adapter intentionally does not implement proxy rotation, CAPTCHA
    solving, cookie harvesting, browser automation, retries that hammer the
    service, or any other anti-bot bypass.  If the public endpoint rejects the
    request, the adapter fails closed and the repricer keeps the current price.

    The endpoint is not treated as a stable official merchant API contract, so
    the response is validated defensively and isolated behind this adapter.
    """

    source_name = "kaspi_public"

    def __init__(
        self,
        *,
        city_id: str = DEFAULT_CITY_ID,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_offers: int = DEFAULT_MAX_OFFERS,
        session: requests.Session | None = None,
    ) -> None:
        self.city_id = str(city_id).strip() or DEFAULT_CITY_ID
        self.timeout_seconds = float(timeout_seconds)
        self.max_offers = int(max_offers)
        self.session = session or requests.Session()

        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 1 <= self.max_offers <= 100:
            raise ValueError("max_offers must be between 1 and 100")

    @staticmethod
    def _product_id(master_sku: str) -> str:
        """Extract the numeric Kaspi card id from an exported SKU.

        Kaspi exports can contain values such as ``116207063_792647100``.
        The public card id is the numeric prefix before the first underscore.
        Plain numeric SKUs are used as-is.  Anything else fails closed.
        """

        raw_sku = str(master_sku or "").strip()
        if not raw_sku:
            raise CompetitorPriceSourceError("Kaspi master_sku пустой.")
        if raw_sku.isdigit():
            return raw_sku

        prefix = raw_sku.split("_", 1)[0].strip()
        if prefix.isdigit():
            return prefix

        raise CompetitorPriceSourceError(
            "Не удалось получить числовой Kaspi product id из master_sku: "
            f"{raw_sku!r}."
        )

    @staticmethod
    def _decimal_price(value: object) -> Decimal | None:
        if value in (None, ""):
            return None
        try:
            price = Decimal(str(value).replace(" ", "").replace(",", "."))
        except (InvalidOperation, ValueError):
            return None
        return price if price > 0 else None

    def fetch_offers(
        self,
        *,
        master_sku: str,
        merchant_sku: str = "",
    ) -> Sequence[KaspiCompetitorOffer]:
        del merchant_sku  # Public offer payload is keyed by Kaspi product id.
        product_id = self._product_id(master_sku)
        url = f"{KASPI_BASE_URL}/yml/offer-view/offers/{product_id}"
        referer = f"{KASPI_BASE_URL}/shop/p/-{product_id}/?c={self.city_id}"
        payload = {
            "cityId": self.city_id,
            "id": product_id,
            "merchantUID": "",
            "limit": self.max_offers,
            "page": 0,
            "sort": True,
            "installationId": "-1",
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": KASPI_BASE_URL,
            "Referer": referer,
            "User-Agent": "ZPT-KZ-KaspiRepricer/1.0 (+https://zpt.kz)",
            "X-KS-City": self.city_id,
            "X-Requested-With": "XMLHttpRequest",
        }

        try:
            response = self.session.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise CompetitorPriceSourceUnavailable(
                f"Kaspi public offers недоступен для {product_id}: {exc}"
            ) from exc

        if response.status_code == 429:
            raise CompetitorPriceSourceRateLimited(
                f"Kaspi ограничил частоту запросов для product_id={product_id}."
            )
        if response.status_code in {401, 403}:
            raise CompetitorPriceSourceUnavailable(
                f"Kaspi отклонил read-only запрос ({response.status_code}) "
                f"для product_id={product_id}."
            )
        if response.status_code >= 400:
            raise CompetitorPriceSourceUnavailable(
                f"Kaspi вернул HTTP {response.status_code} для product_id={product_id}."
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise CompetitorPriceSourceError(
                f"Kaspi вернул не-JSON ответ для product_id={product_id}."
            ) from exc

        raw_offers = data.get("offers") if isinstance(data, dict) else None
        if not isinstance(raw_offers, list):
            raise CompetitorPriceSourceError(
                f"В ответе Kaspi отсутствует массив offers для product_id={product_id}."
            )

        normalized: list[KaspiCompetitorOffer] = []
        for index, raw in enumerate(raw_offers, start=1):
            if not isinstance(raw, dict):
                continue
            price = self._decimal_price(raw.get("price"))
            if price is None:
                continue
            seller_name = str(raw.get("merchantName") or "").strip()
            seller_code = str(raw.get("merchantId") or "").strip()
            if not seller_name and not seller_code:
                continue
            normalized.append(
                KaspiCompetitorOffer(
                    seller_name=seller_name or seller_code,
                    seller_code=seller_code,
                    price=price,
                    position=index,
                    is_available=True,
                )
            )

        return normalized
