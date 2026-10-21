"""Read-only AG Parts catalog API. Not enabled until explicitly configured.

This endpoint only projects owner-approved AG Parts products. No stock
movement, price change, order, external API request, or scheduled job.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from functools import wraps
from urllib.parse import urlsplit

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db import DatabaseError
from django.db.models import Prefetch
from django.http import JsonResponse
from django.utils import timezone

from catalog.agparts_api_manifest import ManifestError, parse_agparts_manifest
from catalog.kaspi_public_url import display_kaspi_public_url
from catalog.models import Product, ProductFitment, ProductImage, ProductWarehouseStock, SellerProfile, Warehouse
from catalog.warehouses import WAREHOUSE_CODE_PP2

logger = logging.getLogger(__name__)
SCHEMA = "agparts.catalog.v1"
MAX_AGE = timedelta(minutes=15)
SUPPLIER_MAX_AGE = timedelta(days=14)
FUTURE_TOLERANCE = timedelta(minutes=1)
PAGE_SIZE = 50
MAX_FITMENTS = 100
MAX_IMAGES = 12
CURSOR_SALT = "agparts-v1-catalog-cursor"
MEDIA_ORIGIN = "https://zpt.kz"


class CatalogUnavailable(Exception):
    """An unverified or stale catalog cannot be sent as public facts."""


def _json(data, status=200):
    response = JsonResponse(data, status=status, json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _gate(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not getattr(settings, "AGPARTS_CATALOG_API_ENABLED", False):
            return _json({"error": "not_found"}, 404)
        token = getattr(settings, "AGPARTS_CATALOG_API_TOKEN", "")
        if not isinstance(token, str) or len(token) < 32:
            return _json({"error": "catalog_not_ready"}, 503)
        supplied = request.headers.get("Authorization", "")
        if len(supplied) > 512 or not hmac.compare_digest(supplied, "Bearer " + token):
            return _json({"error": "not_found"}, 404)
        if request.method != "GET":
            response = _json({"error": "method_not_allowed"}, 405)
            response["Allow"] = "GET"
            return response
        # Defense in depth: per-worker cache cap. At deployment use a shared
        # cache and edge-level throttling too; local memory is not global.
        bucket = timezone.now().strftime("%Y%m%d%H%M")
        key = "agparts:v1:requests:" + hashlib.sha256(token.encode()).hexdigest()[:24] + ":" + bucket
        try:
            if cache.add(key, 1, timeout=65):
                used = 1
            else:
                used = cache.incr(key)
        except Exception:
            return _json({"error": "catalog_not_ready"}, 503)
        if used > 60:
            return _json({"error": "rate_limited"}, 429)
        try:
            return view(request, *args, **kwargs)
        except CatalogUnavailable:
            return _json({"error": "catalog_not_ready"}, 503)
        except (ManifestError, DatabaseError):
            logger.warning("AG Parts catalog projection unavailable (configuration or database)")
            return _json({"error": "catalog_not_ready"}, 503)
    return wrapped


def _recent(stamp, now, limit=MAX_AGE):
    if stamp is None or not isinstance(stamp, datetime):
        return False
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return False
    difference = now - stamp
    return -FUTURE_TOLERANCE <= difference <= limit


def _supply_available(supplier_iso, now):
    if not supplier_iso:
        return False
    try:
        stamp = datetime.fromisoformat(supplier_iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    return _recent(stamp, now, SUPPLIER_MAX_AGE)


def _source_is_https(link):
    try:
        parsed = urlsplit(link or "")
        return bool(parsed.scheme == "https" and parsed.hostname and not parsed.username
                    and not parsed.password and parsed.port in (None, 443) and not parsed.fragment)
    except ValueError:
        return False


def _photo_url(file_field):
    if not file_field:
        return None
    try:
        raw = file_field.url
        parsed = urlsplit(raw)
    except (ValueError, AttributeError):
        return None
    if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment
            or not parsed.path.startswith("/products/") or parsed.path == "/products/"
            or "\\" in parsed.path):
        return None
    return MEDIA_ORIGIN + parsed.path


def _confirmed_fitments(product):
    if not product.fitment_reviewed_on:
        return []
    records = []
    for f in product.fitments.all():
        if f.status != "confirmed" or f.engine_policy not in {"required", "independent", "not_applicable"}:
            continue
        if (f.engine_policy == "required" and not f.engine_code) or (
            f.engine_policy != "required" and f.engine_code
        ):
            continue
        if not f.verified_on or not _source_is_https(f.source_url):
            continue
        records.append({
            "vehicle_make": f.car_model.brand.name,
            "vehicle_model": f.car_model.name,
            "generation": f.generation or "",
            "body": f.body or "",
            "market": f.market or "",
            "fuel": f.fuel or "",
            "power_kw": f.power_kw,
            "engine_policy": f.engine_policy,
            "engine_code": f.engine_code or "",
            "year_from": f.year_from,
            "month_from": f.month_from,
            "year_to": f.year_to,
            "month_to": f.month_to,
            "status": "confirmed",
            "source_url": f.source_url,
            "verified_on": f.verified_on.isoformat(),
            "note": f.note or "",
        })
    if len(records) > MAX_FITMENTS:
        raise CatalogUnavailable("too many fitment rows")
    return records


def _image_list(product):
    images = []
    fields = [product.main_image] + [image.image for image in product.images.all()]
    for file_field in fields:
        url = _photo_url(file_field)
        if url and url not in images:
            images.append(url)
        if len(images) >= MAX_IMAGES:
            break
    return images


def _kaspi_url(product):
    # Stored URLs are not evidence of a currently sellable offer. Until both
    # existing publication flags AND a verified URL agree, export no link.
    if not product.publish_to_kaspi:
        return None
    for listing in product.kaspi_listings.all():
        if not (listing.is_active and listing.publish_to_kaspi
                and listing.public_url_verified_at):
            continue
        link = display_kaspi_public_url(listing.public_url)
        if link:
            return link
    return None


def _project(product, entry, now):
    stock_rows = product.agparts_pp2_rows
    if len(stock_rows) != 1:
        raise CatalogUnavailable("unverified PP2 balance")
    stock = stock_rows[0]
    # updated_at indicates a write, not necessarily a WMS verification. The
    # entire integration must remain off until upstream freshness is proven.
    if not _recent(stock.updated_at, now):
        raise CatalogUnavailable("stale PP2 balance")
    qty = int(stock.quantity)
    if qty < 0:
        raise CatalogUnavailable("negative stock")
    supplier_at = entry["supplier_confirmed_at"] if qty == 0 and _supply_available(entry["supplier_confirmed_at"], now) else None
    status = "in_stock" if qty > 0 else ("supplier_confirmed" if supplier_at else "inquiry")
    price = product.price if not product.price_on_request else None
    if price is not None and (type(price) is not int or price <= 0):
        raise CatalogUnavailable("invalid retail price")
    return {
        "id": product.pk,
        "article": product.article,
        "brand_name": "AG Parts",
        "official_brand_verified": True,
        "status": "active",
        "category": entry["category"],
        "title": entry["title"],
        "retail_price_kzt": price,
        "available_pp2_qty": qty,
        "stock_as_of": stock.updated_at.isoformat(),
        "availability": status,
        "supplier_confirmed_at": supplier_at,
        "lead_time_days": entry["lead_time_days"] if supplier_at else None,
        "confirmed_fitments": _confirmed_fitments(product),
        "images": _image_list(product),
        "kaspi_public_url": _kaspi_url(product),
    }


def _snapshot(now):
    manifest = parse_agparts_manifest(getattr(settings, "AGPARTS_CATALOG_MANIFEST_JSON", ""))
    if not SellerProfile.objects.filter(slug="ag-parts").exists():
        raise CatalogUnavailable("AG Parts seller not found")
    if not Warehouse.objects.filter(code=WAREHOUSE_CODE_PP2, is_active=True).exists():
        raise CatalogUnavailable("PP2 not active")
    rows = list(
        Product.objects.filter(
            seller_profile__slug="ag-parts", status="active",
            article__in=list(manifest),
        ).order_by("pk").prefetch_related(
            Prefetch("warehouse_stocks",
                     queryset=ProductWarehouseStock.objects.filter(warehouse__code=WAREHOUSE_CODE_PP2,
                                                                    warehouse__is_active=True),
                     to_attr="agparts_pp2_rows"),
            Prefetch("fitments", queryset=ProductFitment.objects.select_related("car_model__brand")),
            Prefetch("images", queryset=ProductImage.objects.order_by("sort_order", "pk")),
            "kaspi_listings",
        )
    )
    by_article = {product.article: product for product in rows}
    if len(by_article) != len(manifest) or set(by_article) != set(manifest):
        raise CatalogUnavailable("manifest is not reconciled with active seller products")
    items = [_project(by_article[article], entry, now) for article, entry in manifest.items()]
    items.sort(key=lambda item: item["id"])
    fingerprint = hashlib.sha256(json.dumps(items, sort_keys=True, ensure_ascii=False,
                                           separators=(",", ":")).encode("utf-8")).hexdigest()
    return items, "sha256:" + fingerprint


def _envelope(products, snapshot, now, cursor=None):
    return {
        "schema_version": SCHEMA,
        "snapshot_id": snapshot,
        "generated_at": now.isoformat(),
        "next_cursor": cursor,
        "products": products,
    }


def _prepare():
    now = timezone.now().astimezone(dt_timezone.utc)
    items, snapshot = _snapshot(now)
    return now, items, snapshot


@_gate
def products(request):
    if set(request.GET) - {"cursor"}:
        return _json({"error": "invalid_query"}, 400)
    now, items, snapshot = _prepare()
    offset = 0
    if "cursor" in request.GET:
        cursor = request.GET.get("cursor", "")
        if len(cursor) > 1024:
            return _json({"error": "invalid_cursor"}, 400)
        try:
            state = signing.loads(cursor, salt=CURSOR_SALT, max_age=900)
            if not isinstance(state, dict) or set(state) != {"snapshot", "offset"}:
                raise signing.BadSignature
            if state["snapshot"] != snapshot or type(state["offset"]) is not int:
                raise signing.BadSignature
            offset = state["offset"]
            if offset < 1 or offset >= len(items) or offset % PAGE_SIZE:
                raise signing.BadSignature
        except signing.BadSignature:
            return _json({"error": "snapshot_changed_or_invalid_cursor"}, 409)
    page = items[offset:offset + PAGE_SIZE]
    new_offset = offset + len(page)
    next_cursor = (signing.dumps({"snapshot": snapshot, "offset": new_offset}, salt=CURSOR_SALT)
                   if new_offset < len(items) else None)
    return _json(_envelope(page, snapshot, now, next_cursor))


@_gate
def product_detail(request, product_id):
    if request.GET:
        return _json({"error": "invalid_query"}, 400)
    now, items, snapshot = _prepare()
    selected = next((item for item in items if item["id"] == product_id), None)
    if selected is None:
        return _json({"error": "not_found"}, 404)
    # Same envelope permits the consumer to use its one product-list validator.
    return _json(_envelope([selected], snapshot, now))


@_gate
def vehicles(request):
    if request.GET:
        return _json({"error": "invalid_query"}, 400)
    now, items, snapshot = _prepare()
    variants = []
    seen = set()
    for item in items:
        for f in item["confirmed_fitments"]:
            variant = {"product_id": item["id"], **f}
            key = json.dumps(variant, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                variants.append(variant)
                seen.add(key)
    if len(variants) > 10000:
        raise CatalogUnavailable("too many vehicle variants")
    return _json({
        "schema_version": "agparts.vehicles.v1",
        "snapshot_id": snapshot,
        "generated_at": now.isoformat(),
        "selection_ready": False,
        "fitments": variants,
    })
