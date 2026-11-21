from __future__ import annotations

import json

from django.conf import settings
from django.db.models import OuterRef, Subquery
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from catalog.models import ProductKaspiListing
from repricer.cities import validate_city_id
from repricer.collector_auth import collector_auth_required
from repricer.collector_ingest import CollectorIngestError, ingest_competitor_batch
from repricer.models import KaspiCompetitorIngestBatch

MAX_BODY_BYTES = 256 * 1024


def _json_response(payload: dict, *, status: int = 200) -> JsonResponse:
    response = JsonResponse(payload, status=status)
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _parse_ids(raw: str) -> list[int]:
    values = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        try:
            values.append(int(item))
        except ValueError as exc:
            raise CollectorIngestError("ids must be integers") from exc
    return list(dict.fromkeys(values))


def _manifest_own_merchant_ids() -> list[str]:
    raw = getattr(settings, "KASPI_OWN_MERCHANT_IDS", "") or ""
    ids = [part.strip() for part in str(raw).split(",") if part.strip()]
    return list(dict.fromkeys(ids))


def _manifest_undercut_amount() -> int:
    raw = getattr(settings, "KASPI_REPRICER_UNDERCUT_AMOUNT", 300)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 300
    return 300 if value < 0 else value


@collector_auth_required
@require_GET
def listings_manifest(request):
    raw_city = request.GET.get("city_id", "").strip()
    try:
        city_id = validate_city_id(raw_city) if raw_city else ""
    except ValueError:
        return _json_response({"error": "invalid city_id"}, status=400)
    last_batches = KaspiCompetitorIngestBatch.objects.filter(
        listing_id=OuterRef("pk"), source="office_collector",
    )
    if city_id:
        last_batches = last_batches.filter(city_id=city_id)
    last_collected = last_batches.order_by("-captured_at").values("captured_at")[:1]
    queryset = ProductKaspiListing.objects.select_related("product").filter(
        is_active=True,
    ).annotate(last_collected_at=Subquery(last_collected))
    raw_ids = request.GET.get("ids", "")
    if raw_ids.strip():
        try:
            listing_ids = _parse_ids(raw_ids)
        except CollectorIngestError as exc:
            return _json_response({"error": exc.message}, status=exc.status)
        queryset = queryset.filter(pk__in=listing_ids)
    rows = [
        {
            "listing_id": listing.pk,
            "article": listing.product.article or "",
            "master_sku": listing.master_sku,
            "merchant_sku": listing.merchant_sku or "",
            "last_known_our_price": listing.last_known_our_price,
            "public_url": listing.public_url or "",
            "last_collected_at": (
                listing.last_collected_at.isoformat() if listing.last_collected_at else None
            ),
        }
        for listing in queryset.order_by("id")
    ]
    payload = {
        "own_merchant_ids": _manifest_own_merchant_ids(),
        "undercut_amount": _manifest_undercut_amount(),
        "listings": rows,
    }
    return _json_response(payload)


@collector_auth_required
@require_POST
def ingest_batches(request):
    content_type = request.META.get("CONTENT_TYPE") or request.content_type or ""
    if "application/json" not in content_type.lower():
        return _json_response({"error": "JSON only"}, status=415)
    length_header = request.META.get("CONTENT_LENGTH") or ""
    if length_header.isdigit() and int(length_header) > MAX_BODY_BYTES:
        return _json_response({"error": "payload too large"}, status=413)
    body = request.body or b""
    if len(body) > MAX_BODY_BYTES:
        return _json_response({"error": "payload too large"}, status=413)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _json_response({"error": "malformed JSON"}, status=400)
    try:
        result = ingest_competitor_batch(payload)
    except CollectorIngestError as exc:
        return _json_response({"error": exc.message}, status=exc.status)
    return _json_response(
        {
            "ok": True,
            "duplicate": result.duplicate,
            "snapshots_created": result.snapshots_created,
            "batch_id": result.batch_id,
            "listing_id": result.listing_id,
        }
    )
