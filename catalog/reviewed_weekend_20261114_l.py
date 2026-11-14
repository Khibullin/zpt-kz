"""One exact JASHI fitment from an exact OE cross; never changes photo, trade, or Kaspi data."""
import hashlib
import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

MANIFEST_SHA256 = "12ab066f6384e8e439614283ce9daae5a66b74054080ffae9d45967b0c01f7ab"
BATCH = "weekend-20261114-l"

@lru_cache(maxsize=1)
def reviewed_rows():
    raw = (Path(__file__).parent / "data" / "reviewed_weekend_20261114_l.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise ValueError("Reviewed JASHI fitment manifest changed")
    payload = json.loads(raw)
    rows = payload["sources"]
    if payload["checked_on"] != "2026-11-14" or len(rows) != 1:
        raise ValueError("Unexpected JASHI fitment batch")
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
            product = listing.product
            if product.status != "active" or product.article != row["article"]:
                continue
            if Fitment.objects.filter(offer_id=offer.pk).exists():
                continue
            for fit in row["fitments"]:
                Fitment.objects.create(
                    offer_id=offer.pk,
                    make=fit["make"],
                    model=fit["model"],
                    details=fit["details"],
                    configurations=fit["configurations"],
                    source_url=row["source_url"],
                    verified=True,
                )
            metadata = dict(offer.characteristics or {})
            metadata["reviewed_applicability"] = {
                "source_label": row["source_label"],
                "source_kind": row["source_kind"],
                "source_urls": row["source_urls"],
                "checked_on": "2026-11-14",
                "source_article": row["manufacturer_article"],
                "source_brand": "JASHI",
                "status": "sourced",
                "note": row["fitment_note"],
            }
            offer.characteristics = metadata
            offer.save(update_fields=["characteristics"])
            counts["fitment_offers"] += 1
            counts["make_model_pairs"] += len(row["fitments"])
            counts["configuration_rows"] += sum(len(fit["configurations"]) for fit in row["fitments"])
        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_jashi_cross_fitment_import",
            details={"batch": BATCH, **counts},
        )
