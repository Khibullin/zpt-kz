"""Read-only competitor snapshot display for Control.

This module never fetches Kaspi, never writes rows, and never computes a
recommended price.  It only interprets already stored offer snapshots.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable

from django.conf import settings
from django.db.models import Max, Q
from django.utils import timezone

from repricer.models import KaspiCompetitorOfferSnapshot
from repricer.services import configured_own_merchants

STATE_NO_DATA = "NO_DATA"
STATE_OWN_MERCHANT_NOT_CONFIGURED = "OWN_MERCHANT_NOT_CONFIGURED"
STATE_NO_OTHER_OFFERS = "NO_OTHER_OFFERS"
STATE_READY = "READY"
STATE_STALE = "STALE"

PUBLIC_SOURCE = "kaspi_public"
COLLECTOR_SOURCE = "office_collector"
PUBLIC_EQUIVALENT_SOURCES = frozenset({PUBLIC_SOURCE, COLLECTOR_SOURCE})
DEFAULT_FRESH_MINUTES = 180

STATE_LABELS = {
    STATE_NO_DATA: "Нет данных",
    STATE_OWN_MERCHANT_NOT_CONFIGURED: "Продавец не настроен",
    STATE_NO_OTHER_OFFERS: "Нет других",
    STATE_READY: "Актуально",
    STATE_STALE: "Устарело",
}

TOOLTIP_NO_DATA = "Данные конкурентов ещё не получены"
TOOLTIP_OWN_MERCHANT_NOT_CONFIGURED = "Не настроен собственный продавец Kaspi"


@dataclass(frozen=True)
class ListingCompetitorState:
    listing_id: int
    has_snapshot: bool
    captured_at: datetime | None
    competitor_count: int
    best_price: Decimal | None
    best_seller_name: str
    best_seller_code: str
    is_fresh: bool
    is_stale: bool
    state: str

    @property
    def state_label(self) -> str:
        return STATE_LABELS.get(self.state, self.state)


def competitor_fresh_minutes() -> int:
    raw = getattr(settings, "KASPI_COMPETITOR_FRESH_MINUTES", DEFAULT_FRESH_MINUTES)
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_FRESH_MINUTES
    return minutes if minutes > 0 else DEFAULT_FRESH_MINUTES


def _empty_state(listing_id: int) -> ListingCompetitorState:
    return ListingCompetitorState(
        listing_id=listing_id,
        has_snapshot=False,
        captured_at=None,
        competitor_count=0,
        best_price=None,
        best_seller_name="",
        best_seller_code="",
        is_fresh=False,
        is_stale=False,
        state=STATE_NO_DATA,
    )


def _is_own_offer(seller_code: str, seller_name: str, own_ids: set[str], own_names: set[str]) -> bool:
    code = (seller_code or "").strip().casefold()
    name = (seller_name or "").strip().casefold()
    if code and code in own_ids:
        return True
    if name and name in own_names:
        return True
    return False


def _own_merchant_configured(own_ids: set[str], own_names: set[str]) -> bool:
    return bool(own_ids or own_names)


def _state_from_batch(
    *,
    listing_id: int,
    batch: list[KaspiCompetitorOfferSnapshot],
    now: datetime,
    fresh_delta: timedelta,
    own_ids: set[str],
    own_names: set[str],
) -> ListingCompetitorState:
    captured_at = batch[0].captured_at
    is_fresh = captured_at >= now - fresh_delta
    is_stale = not is_fresh
    has_public = any(item.source in PUBLIC_EQUIVALENT_SOURCES for item in batch)
    if has_public and not _own_merchant_configured(own_ids, own_names):
        return ListingCompetitorState(
            listing_id=listing_id,
            has_snapshot=True,
            captured_at=captured_at,
            competitor_count=0,
            best_price=None,
            best_seller_name="",
            best_seller_code="",
            is_fresh=is_fresh,
            is_stale=is_stale,
            state=STATE_OWN_MERCHANT_NOT_CONFIGURED,
        )

    others = [
        item
        for item in batch
        if item.is_available
        and not _is_own_offer(item.seller_code, item.seller_name, own_ids, own_names)
    ]
    if not others:
        return ListingCompetitorState(
            listing_id=listing_id,
            has_snapshot=True,
            captured_at=captured_at,
            competitor_count=0,
            best_price=None,
            best_seller_name="",
            best_seller_code="",
            is_fresh=is_fresh,
            is_stale=is_stale,
            state=STATE_NO_OTHER_OFFERS,
        )

    best = min(others, key=lambda item: (item.price, item.pk))
    return ListingCompetitorState(
        listing_id=listing_id,
        has_snapshot=True,
        captured_at=captured_at,
        competitor_count=len(others),
        best_price=best.price,
        best_seller_name=best.seller_name,
        best_seller_code=best.seller_code,
        is_fresh=is_fresh,
        is_stale=is_stale,
        state=STATE_STALE if is_stale else STATE_READY,
    )


def competitor_states_for_listings(
    listing_ids: Iterable[int],
    *,
    now: datetime | None = None,
) -> dict[int, ListingCompetitorState]:
    """Return latest-batch competitor state for each listing.

    Uses at most two SQL queries for the whole page, never MIN(price) over
    historical captures.
    """

    ids = list(dict.fromkeys(int(pk) for pk in listing_ids))
    result = {pk: _empty_state(pk) for pk in ids}
    if not ids:
        return result

    latest_rows = list(
        KaspiCompetitorOfferSnapshot.objects.filter(listing_id__in=ids)
        .values("listing_id")
        .annotate(latest_at=Max("captured_at"))
    )
    if not latest_rows:
        return result

    batch_q = Q()
    for row in latest_rows:
        batch_q |= Q(listing_id=row["listing_id"], captured_at=row["latest_at"])

    offers = KaspiCompetitorOfferSnapshot.objects.filter(batch_q).order_by("price", "id")
    grouped: dict[int, list[KaspiCompetitorOfferSnapshot]] = defaultdict(list)
    for offer in offers:
        grouped[offer.listing_id].append(offer)

    clock = now or timezone.now()
    fresh_delta = timedelta(minutes=competitor_fresh_minutes())
    own_ids, own_names = configured_own_merchants()
    for listing_id, batch in grouped.items():
        result[listing_id] = _state_from_batch(
            listing_id=listing_id,
            batch=batch,
            now=clock,
            fresh_delta=fresh_delta,
            own_ids=own_ids,
            own_names=own_names,
        )
    return result


def listing_competitor_state(
    listing_id: int,
    *,
    now: datetime | None = None,
) -> ListingCompetitorState:
    return competitor_states_for_listings([listing_id], now=now)[listing_id]
