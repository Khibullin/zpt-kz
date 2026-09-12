from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from catalog.models import ProductKaspiListing
from integrations.kaspi_competitors import KaspiCompetitorPriceSource

from .models import KaspiCompetitorOfferSnapshot


@dataclass(frozen=True)
class CompetitorSyncResult:
    listing_id: int
    master_sku: str
    offers_received: int
    snapshots_created: int
    captured_at: datetime


@transaction.atomic
def sync_competitor_offers_for_listing(
    *,
    listing: ProductKaspiListing,
    source: KaspiCompetitorPriceSource,
    source_name: str = "kaspi_public",
    captured_at: datetime | None = None,
) -> CompetitorSyncResult:
    """Fetch one listing and append normalized snapshots.

    The function is deliberately append-only: historical observations are not
    rewritten or deleted.  It also never updates our Kaspi price and never
    sends any write request to Kaspi.
    """

    timestamp = captured_at or timezone.now()
    offers = list(
        source.fetch_offers(
            master_sku=listing.master_sku,
            merchant_sku=listing.merchant_sku,
        )
    )

    rows = [
        KaspiCompetitorOfferSnapshot(
            listing=listing,
            seller_name=offer.seller_name,
            seller_code=offer.seller_code,
            price=offer.price,
            position=offer.position,
            is_available=offer.is_available,
            source=source_name,
            captured_at=timestamp,
        )
        for offer in offers
        if offer.is_available
    ]
    if rows:
        KaspiCompetitorOfferSnapshot.objects.bulk_create(rows)

    return CompetitorSyncResult(
        listing_id=listing.pk,
        master_sku=listing.master_sku,
        offers_received=len(offers),
        snapshots_created=len(rows),
        captured_at=timestamp,
    )
