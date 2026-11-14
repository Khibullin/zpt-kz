"""Enhance one exact Changan fitment; never changes photo, trade, or Kaspi data."""
import hashlib
import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

MANIFEST_SHA256 = "8a63170ab6a5acbb3edade0d421fba2c6e63942bc7e0c730f66ebfe420306253"
BATCH = "weekend-20261114-m"


@lru_cache(maxsize=1)
def reviewed_rows():
    raw = (Path(__file__).parent / "data" / "reviewed_weekend_20261114_m.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise ValueError("Reviewed Changan fitment manifest changed")
    payload = json.loads(raw)
    rows = payload["sources"]
    if payload["checked_on"] != "2026-11-14" or len(rows) != 1:
        raise ValueError("Unexpected Changan fitment batch")
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

    counts = {"fitment_offers": 0, "configuration_rows": 0}
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

            fitment = Fitment.objects.select_for_update().filter(
                pk=row["fitment_id"],
                offer_id=offer.pk,
                verified=True,
            ).first()
            expected = row["expected_before"]
            if not fitment or any(
                [
                    fitment.make != expected["make"],
                    fitment.model != expected["model"],
                    fitment.details != expected["details"],
                    fitment.source_url != expected["source_url"],
                    fitment.configurations != expected["configurations"],
                ]
            ):
                continue

            fitment.details = row["details"]
            fitment.configurations = row["configurations"]
            fitment.source_url = row["source_url"]
            fitment.save(update_fields=["details", "configurations", "source_url"])

            metadata = dict(offer.characteristics or {})
            metadata["reviewed_applicability"] = {
                "source_label": row["source_label"],
                "source_kind": row["source_kind"],
                "source_urls": row["source_urls"],
                "checked_on": "2026-11-14",
                "source_article": row["manufacturer_article"],
                "source_brand": "Changan",
                "status": "sourced",
                "note": row["fitment_note"],
                "photo_review": row["photo_review"],
            }
            offer.characteristics = metadata
            offer.save(update_fields=["characteristics"])
            counts["fitment_offers"] += 1
            counts["configuration_rows"] += len(row["configurations"])

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_changan_fitment_enhancement",
            details={"batch": BATCH, **counts},
        )

