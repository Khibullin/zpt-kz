"""Strict, manually approved AG Parts catalog manifest.

Only the owner-approved manifest grants brand/category publication.
No DB writes, networks, inferred product categories, or automatic approvals.
"""
import json
from datetime import datetime

ALLOWED_TYPES = frozenset({"cabin", "air", "oil", "spark"})
MAX_MANIFEST_BYTES = 250_000
MAX_PRODUCTS = 1000
ENTRY_FIELDS = frozenset({
    "official_brand_verified", "category", "title",
    "supplier_confirmed_at", "lead_time_days", "kaspi_pack_qty",
})


class ManifestError(ValueError):
    """Configuration is absent or not explicitly approved."""


def parse_agparts_manifest(raw):
    if not isinstance(raw, str) or not raw.strip() or len(raw.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise ManifestError("missing or oversized manifest")
    try:
        document = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ManifestError("invalid manifest JSON") from exc
    if not isinstance(document, dict) or set(document) != {"version", "products"}:
        raise ManifestError("invalid manifest shape")
    if type(document["version"]) is not int or document["version"] != 1:
        raise ManifestError("unsupported manifest version")
    entries = document["products"]
    if not isinstance(entries, dict) or not 1 <= len(entries) <= MAX_PRODUCTS:
        raise ManifestError("manifest must contain approved product entries")
    approved = {}
    seen = set()
    for article, entry in entries.items():
        if not isinstance(article, str) or not 1 <= len(article) <= 100 or article != article.strip():
            raise ManifestError("invalid literal article")
        identity = article.casefold()
        if identity in seen:
            raise ManifestError("duplicate article (case-insensitive)")
        seen.add(identity)
        if not isinstance(entry, dict) or set(entry) != ENTRY_FIELDS:
            raise ManifestError("invalid manifest product fields")
        if entry["official_brand_verified"] is not True:
            raise ManifestError("product brand not explicitly verified")
        if type(entry["category"]) is not str or entry["category"] not in ALLOWED_TYPES:
            raise ManifestError("invalid official product category")
        titles = entry["title"]
        if not isinstance(titles, dict) or set(titles) != {"ru", "kk", "zh"}:
            raise ManifestError("three approved product translations required")
        if any(not isinstance(v, str) or not v.strip() or len(v) > 255 for v in titles.values()):
            raise ManifestError("empty or overlong translation")
        pack_qty = entry["kaspi_pack_qty"]
        if pack_qty is not None and (type(pack_qty) is not int or pack_qty < 1 or pack_qty > 100):
            raise ManifestError("kaspi_pack_qty must be an explicitly verified count (1..100) or null")
        confirmed = entry["supplier_confirmed_at"]
        days = entry["lead_time_days"]
        if confirmed is not None:
            if not isinstance(confirmed, str) or len(confirmed) > 40:
                raise ManifestError("invalid supplier confirmation")
            try:
                timestamp = datetime.fromisoformat(confirmed.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ManifestError("invalid confirmation timestamp") from exc
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ManifestError("confirmation timezone is required")
        if days is not None and (type(days) is not int or not 1 <= days <= 365):
            raise ManifestError("invalid supplier lead time")
        if confirmed is None and days is not None:
            raise ManifestError("lead time without confirmed supplier")
        approved[article] = entry
    return approved
