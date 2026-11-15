"""Stage one exact HAVAL photo for the sole existing consumer; preserve fitment and trade data."""
import hashlib
import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

MANIFEST_SHA256 = "baaca20e620dc5cdb9d02779865337494ed65b9eeb7ea1c04aa3e66e3d6386d9"
BATCH = "weekend-20261114-n"
TASK_STATUS = "weekend14n_ready"


@lru_cache(maxsize=1)
def reviewed_rows():
    raw = (Path(__file__).parent / "data" / "reviewed_weekend_20261114_n.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise ValueError("Reviewed HAVAL photo manifest changed")
    payload = json.loads(raw)
    rows = payload["sources"]
    if payload["checked_on"] != "2026-11-14" or len(rows) != 1:
        raise ValueError("Unexpected HAVAL photo batch")
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
    Listing = apps.get_model("procurement", "RetailListing")
    Activity = apps.get_model("procurement", "Activity")
    Supplier = apps.get_model("procurement", "Supplier")
    Task = apps.get_model("catalog", "SupplierPhotoTask")
    if not Supplier.objects.filter(pk=5, name="ZakazAuto — Костанай").exists():
        return

    counts = {"photo_tasks": 0}
    with transaction.atomic():
        for row in reviewed_rows():
            offer = Offer.objects.select_for_update().filter(pk=row["offer_id"]).first()
            listing = Listing.objects.select_related("product").filter(
                offer_id=row["offer_id"], product_id=row["product_id"], enabled=True
            ).first()
            if not exact_offer(offer, row) or listing is None:
                continue
            product = listing.product
            if product.status != "active" or product.article != row["article"] or product.main_image:
                continue
            task = Task.objects.select_for_update().select_related("offer").filter(
                pk=row["task_id"], product_id=product.pk, offer_id=offer.pk, status="needs_source"
            ).first()
            if not task or task.offer.brand != row["brand"] or task.offer.article != row["article"]:
                continue
            task.source_url = row["source_url"]
            task.status = TASK_STATUS
            task.note = "Точная карточка и фотография HAVAL 2905110AKZ16A проверены; ожидает единственного существующего потребителя."
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": BATCH,
                "reviewed_manifest_sha256": MANIFEST_SHA256,
                "source_kind": row["source_kind"],
                "source_label": row["source_label"],
                "source_url": row["source_url"],
                "verified_image_url": row["verified_image_url"],
                "verified_image_sha256": row["verified_image_sha256"],
                "verified_image_size": row["verified_image_size"],
                "verification_note": row["verification_note"],
                "checked_on": "2026-11-14",
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            counts["photo_tasks"] += 1
        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_haval_photo_import",
            details={"batch": BATCH, **counts},
        )

