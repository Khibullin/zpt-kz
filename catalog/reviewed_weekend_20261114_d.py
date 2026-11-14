"""One exact weekend fitment; never changes photos, trade, stock, or Kaspi data."""
import hashlib
import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

MANIFEST_SHA256 = "6231fb522972f81500a2729c1aad868f4bb3c324e9d9728216ab2d03a215785c"
BATCH = "weekend-20261114-d"


@lru_cache(maxsize=1)
def reviewed_rows():
    raw = (Path(__file__).parent / "data" / "reviewed_weekend_20261114_d.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise ValueError("Reviewed weekend fitment manifest changed")
    payload = json.loads(raw)
    rows = payload["sources"]
    if payload["checked_on"] != "2026-11-14" or len(rows) != 1:
        raise ValueError("Unexpected weekend fitment batch")
    return rows


def exact_offer(offer, row):
    from procurement.analysis import stock_status

    return bool(
        offer
        and offer.pk == row["offer_id"]
        and offer.supplier_id == row["supplier_id"] == 5
        and offer.brand == row["brand"]
        and offer.article == row["article"]
        and stock_status(offer.stock) == "in_stock"
        and str(offer.stock) == row["stock"]
        and offer.cost == Decimal(row["cost"])
    )


def stage_review(apps, schema_editor):
    """Add only the absent exact fitment after locking and rechecking the live row."""
    if schema_editor.connection.alias != "default":
        return
    from django.db import transaction

    Offer = apps.get_model("procurement", "Offer")
    Fitment = apps.get_model("procurement", "VehicleFitment")
    Listing = apps.get_model("procurement", "RetailListing")
    Activity = apps.get_model("procurement", "Activity")
    Supplier = apps.get_model("procurement", "Supplier")
    if not Supplier.objects.filter(pk=5, name="ZakazAuto — Костанай").exists():
        return

    counts = {"fitment_offers": 0, "make_model_pairs": 0, "configuration_rows": 0}
    with transaction.atomic():
        for row in reviewed_rows():
            offer = Offer.objects.select_for_update().filter(pk=row["offer_id"]).first()
            listing = Listing.objects.select_related("product").filter(
                offer_id=row["offer_id"], product_id=row["product_id"], enabled=True
            ).first()
            if not exact_offer(offer, row) or listing is None:
                continue
            if Fitment.objects.filter(offer_id=offer.pk).exists():
                continue

            grouped = {}
            for fit in row["fitments"]:
                key = (fit["make"], fit["model"])
                payload = grouped.setdefault(key, {"details": fit.get("details", ""), "configs": []})
                if payload["details"] != fit.get("details", ""):
                    raise ValueError("Conflicting fitment details for one make/model pair")
                config = {
                    key: value
                    for key, value in fit.items()
                    if key not in ("make", "model", "details")
                }
                if config not in payload["configs"]:
                    payload["configs"].append(config)
            for (make, model), payload in grouped.items():
                Fitment.objects.create(
                    offer_id=offer.pk,
                    make=make,
                    model=model,
                    details=payload["details"],
                    configurations=payload["configs"],
                    source_url=payload["configs"][0]["source_url"],
                    verified=True,
                )
            metadata = dict(offer.characteristics or {})
            metadata["reviewed_applicability"] = {
                "source_label": row["source_label"],
                "source_kind": row["source_kind"],
                "source_urls": row["source_urls"],
                "checked_on": "2026-11-14",
                "source_article": row["article"],
                "source_brand": row["brand"],
                "status": "sourced",
                "note": row["fitment_note"],
            }
            offer.characteristics = metadata
            offer.save(update_fields=["characteristics"])
            counts["fitment_offers"] += 1
            counts["make_model_pairs"] += len(grouped)
            counts["configuration_rows"] += len(row["fitments"])
        Activity.objects.create(user_id=None, action="reviewed_weekend_fitment_import", details={"batch": BATCH, **counts})
