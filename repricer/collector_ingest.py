from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.db import transaction
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from catalog.models import ProductKaspiListing
from repricer.models import KaspiCompetitorIngestBatch, KaspiCompetitorOfferSnapshot

COLLECTOR_SOURCE = "office_collector"
MAX_OFFERS = 100
MAX_SELLER_NAME = 255
MAX_SELLER_CODE = 128


class CollectorIngestError(ValueError):
    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class IngestResult:
    batch_id: str
    listing_id: int
    snapshots_created: int
    duplicate: bool


def _require_listing(listing_id: object, master_sku: object) -> ProductKaspiListing:
    try:
        pk = int(listing_id)
    except (TypeError, ValueError) as exc:
        raise CollectorIngestError("listing_id must be an integer") from exc
    if not isinstance(master_sku, str) or not master_sku.strip():
        raise CollectorIngestError("master_sku is required")
    listing = (
        ProductKaspiListing.objects.select_related("product")
        .filter(pk=pk)
        .first()
    )
    if listing is None:
        raise CollectorIngestError("listing not found")
    if not listing.is_active:
        raise CollectorIngestError("listing is inactive")
    if listing.master_sku != master_sku:
        raise CollectorIngestError("master_sku does not match listing")
    return listing


def _parse_batch_id(value: object) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise CollectorIngestError("batch_id must be a UUID") from exc


def _parse_captured_at(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise CollectorIngestError("captured_at is required")
    parsed = parse_datetime(value.strip())
    if parsed is None:
        raise CollectorIngestError("captured_at is invalid")
    if timezone.is_naive(parsed):
        raise CollectorIngestError("captured_at must be timezone-aware")
    return parsed


def _parse_price(value: object) -> Decimal:
    if value in (None, ""):
        raise CollectorIngestError("price is required")
    try:
        price = Decimal(str(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise CollectorIngestError("price is invalid") from exc
    if price <= 0:
        raise CollectorIngestError("price must be greater than 0")
    if price.as_tuple().exponent < -2:
        raise CollectorIngestError("price has too many decimal places")
    digits = len(price.as_tuple().digits)
    if digits > 14:
        raise CollectorIngestError("price is too large")
    return price


def _parse_position(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise CollectorIngestError("position must be a positive integer or null")
    return value


def _parse_offer(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise CollectorIngestError("each offer must be an object")
    seller_name = raw.get("seller_name")
    seller_code = raw.get("seller_code", "")
    if seller_name is None:
        seller_name = ""
    if seller_code is None:
        seller_code = ""
    if not isinstance(seller_name, str) or not isinstance(seller_code, str):
        raise CollectorIngestError("seller_name and seller_code must be strings")
    seller_name = seller_name.strip()
    seller_code = seller_code.strip()
    if len(seller_name) > MAX_SELLER_NAME or len(seller_code) > MAX_SELLER_CODE:
        raise CollectorIngestError("seller_name or seller_code is too long")
    if not seller_name and not seller_code:
        raise CollectorIngestError("seller_name or seller_code is required")
    is_available = raw.get("is_available", True)
    if not isinstance(is_available, bool):
        raise CollectorIngestError("is_available must be a boolean")
    return {
        "seller_name": seller_name or seller_code,
        "seller_code": seller_code,
        "price": _parse_price(raw.get("price")),
        "position": _parse_position(raw.get("position")),
        "is_available": is_available,
    }


def _parse_offers(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        raise CollectorIngestError("offers must be a list")
    if not raw:
        raise CollectorIngestError("offers must not be empty")
    if len(raw) > MAX_OFFERS:
        raise CollectorIngestError("too many offers")
    return [_parse_offer(item) for item in raw]


def ingest_competitor_batch(payload: dict) -> IngestResult:
    if not isinstance(payload, dict):
        raise CollectorIngestError("JSON object required")
    listing = _require_listing(payload.get("listing_id"), payload.get("master_sku"))
    batch_id = _parse_batch_id(payload.get("batch_id"))
    captured_at = _parse_captured_at(payload.get("captured_at"))
    offers = _parse_offers(payload.get("offers"))

    with transaction.atomic():
        existing = (
            KaspiCompetitorIngestBatch.objects.select_for_update()
            .filter(external_batch_id=batch_id)
            .first()
        )
        if existing is not None:
            if existing.listing_id != listing.pk:
                raise CollectorIngestError("batch_id already used for another listing", status=409)
            return IngestResult(
                batch_id=str(batch_id),
                listing_id=listing.pk,
                snapshots_created=0,
                duplicate=True,
            )

        batch = KaspiCompetitorIngestBatch.objects.create(
            external_batch_id=batch_id,
            listing=listing,
            source=COLLECTOR_SOURCE,
            captured_at=captured_at,
            offer_count=len(offers),
        )
        rows = [
            KaspiCompetitorOfferSnapshot(
                listing=listing,
                seller_name=offer["seller_name"],
                seller_code=offer["seller_code"],
                price=offer["price"],
                position=offer["position"],
                is_available=offer["is_available"],
                source=COLLECTOR_SOURCE,
                captured_at=captured_at,
            )
            for offer in offers
            if offer["is_available"]
        ]
        created = 0
        if rows:
            KaspiCompetitorOfferSnapshot.objects.bulk_create(rows)
            created = len(rows)
        return IngestResult(
            batch_id=str(batch.external_batch_id),
            listing_id=listing.pk,
            snapshots_created=created,
            duplicate=False,
        )
