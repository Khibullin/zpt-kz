"""Manual, browser-driven Kaspi offer collection for the office.

Each POST performs at most one read-only Kaspi request. No background worker,
scheduler, pricing rules or writeback to Kaspi are involved.
"""
from __future__ import annotations

from uuid import uuid4

from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from catalog.models import ProductKaspiListing
from integrations.kaspi_competitors import (
    CompetitorPriceSourceError,
    CompetitorPriceSourceRateLimited,
    KaspiPublicOfferSource,
    kaspi_public_product_id,
)
from .cities import CITY_BY_ID, KASPI_KZ_CITIES
from .models import (
    KaspiCompetitorIngestBatch, KaspiCompetitorOfferSnapshot,
    KaspiManualCollectionRun, KaspiActiveExportRow,
)
from .views import _latest_active_batch

MAX_OFFERS_PER_REQUEST = 100


def _response(run, *, current=None):
    return JsonResponse({
        "id": run.pk,
        "status": run.status,
        "processed": run.next_index,
        "total": run.total,
        "saved_offers": run.saved_offers,
        "empty_count": run.empty_count,
        "skipped_count": run.skipped_count,
        "error_count": run.error_count,
        "last_error": run.last_error,
        "current": current or "",
    })


@staff_member_required
@require_POST
def manual_start(request):
    selection = request.POST.get("city_id", "750000000")
    if selection == "all":
        cities = [city for city, _name in KASPI_KZ_CITIES]
    elif selection in CITY_BY_ID:
        cities = [selection]
    else:
        return JsonResponse({"error": "Неизвестный город Kaspi"}, status=400)

    existing = KaspiManualCollectionRun.objects.filter(
        created_by=request.user, status=KaspiManualCollectionRun.Status.RUNNING,
    ).first()
    if existing:
        return _response(existing)

    # The latest complete ACTIVE upload, not historical catalog bindings,
    # defines which of our products are eligible for competitor monitoring.
    active_batch = _latest_active_batch()
    if active_batch:
        ids = list(
            KaspiActiveExportRow.objects.filter(
                batch=active_batch, listing__is_active=True,
            ).exclude(listing__isnull=True)
            .order_by("row_number").values_list("listing_id", flat=True).distinct()
        )
    else:
        ids = list(
            ProductKaspiListing.objects.filter(is_active=True)
            .order_by("pk").values_list("pk", flat=True)
        )
    if not ids:
        return JsonResponse({"error": "Активных Kaspi-привязок нет"}, status=400)
    run = KaspiManualCollectionRun.objects.create(
        created_by=request.user, city_ids=cities, listing_ids=ids,
    )
    return _response(run)


@staff_member_required
@require_POST
def manual_pause(request, run_id):
    with transaction.atomic():
        run = get_object_or_404(
            KaspiManualCollectionRun.objects.select_for_update(),
            pk=run_id, created_by=request.user,
        )
        if run.status == KaspiManualCollectionRun.Status.RUNNING:
            run.status = KaspiManualCollectionRun.Status.STOPPED
            run.save(update_fields=["status", "updated_at"])
    return _response(run)


@staff_member_required
@require_POST
def manual_resume(request, run_id):
    with transaction.atomic():
        run = get_object_or_404(
            KaspiManualCollectionRun.objects.select_for_update(),
            pk=run_id, created_by=request.user,
        )
        if run.status == KaspiManualCollectionRun.Status.STOPPED and run.next_index < run.total:
            run.status = KaspiManualCollectionRun.Status.RUNNING
            run.last_error = ""
            run.save(update_fields=["status", "last_error", "updated_at"])
    return _response(run)


@staff_member_required
@require_POST
def manual_step(request, run_id):
    # Row lock serializes concurrent browser requests; one Kaspi call per step.
    with transaction.atomic():
        run = get_object_or_404(
            KaspiManualCollectionRun.objects.select_for_update(),
            pk=run_id, created_by=request.user,
        )
        if run.status != KaspiManualCollectionRun.Status.RUNNING:
            return _response(run)
        if run.next_index >= run.total:
            run.status = KaspiManualCollectionRun.Status.DONE
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at", "updated_at"])
            return _response(run)

        city_index = run.next_index // len(run.listing_ids)
        listing_index = run.next_index % len(run.listing_ids)
        city_id = run.city_ids[city_index]
        listing_id = run.listing_ids[listing_index]
        city_name = CITY_BY_ID.get(city_id, city_id)
        listing = ProductKaspiListing.objects.filter(
            pk=listing_id, is_active=True,
        ).first()

        current = f"{city_name}, товар {listing_index + 1}/{len(run.listing_ids)}"
        if listing is None or kaspi_public_product_id(
            listing.master_sku, merchant_sku=listing.merchant_sku or "",
            public_url=listing.public_url or "",
        ) is None:
            run.skipped_count += 1
        else:
            source = KaspiPublicOfferSource(
                city_id=city_id, timeout_seconds=8, max_offers=MAX_OFFERS_PER_REQUEST,
            )
            try:
                offers = list(source.fetch_offers(
                    master_sku=listing.master_sku,
                    merchant_sku=listing.merchant_sku or "",
                    public_url=listing.public_url or "",
                ))
            except CompetitorPriceSourceError as exc:
                # Fail closed on 403/405/429 or source changes; do not keep retrying.
                run.error_count += 1
                run.last_error = (f"{city_name}: {exc}")[:500]
                run.status = KaspiManualCollectionRun.Status.STOPPED
                run.save(update_fields=[
                    "error_count", "last_error", "status", "updated_at",
                ])
                return _response(run, current=current)
            captured = timezone.now()
            KaspiCompetitorIngestBatch.objects.create(
                external_batch_id=uuid4(), listing=listing,
                source="office_collector", city_id=city_id,
                captured_at=captured, offer_count=len(offers),
            )
            rows = [
                KaspiCompetitorOfferSnapshot(
                    listing=listing, seller_name=offer.seller_name,
                    seller_code=offer.seller_code, price=offer.price,
                    position=offer.position, is_available=offer.is_available,
                    source="office_collector", city_id=city_id,
                    captured_at=captured,
                )
                for offer in offers if offer.is_available
            ]
            if rows:
                KaspiCompetitorOfferSnapshot.objects.bulk_create(rows)
                run.saved_offers += len(rows)
            else:
                run.empty_count += 1

        run.next_index += 1
        if run.next_index >= run.total:
            run.status = KaspiManualCollectionRun.Status.DONE
            run.finished_at = timezone.now()
        run.save()
        return _response(run, current=current)
