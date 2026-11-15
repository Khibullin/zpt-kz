"""One exact, bounded nightly photo and fitment; never changes trade or Kaspi data."""
import base64
import hashlib
import json
import shutil
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

MANIFEST_SHA256 = "4df7f41fa12dbb807753ec03ec1d8737f81d283a8d77c2005af988ab376ca76e"
BATCH = "nightly-20261114-d"
TASK_STATUS = "nightly14d_ready"


@lru_cache(maxsize=1)
def reviewed_rows():
    raw = (Path(__file__).parent / "data" / "reviewed_nightly_20261114_d.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise ValueError("Reviewed nightly photo and fitment manifest changed")
    payload = json.loads(raw)
    rows = payload["sources"]
    if payload["checked_on"] != "2026-11-14" or len(rows) != 1:
        raise ValueError("Unexpected nightly photo and fitment batch")
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
    """Add only absent exact fitments and stage one already reviewed photograph."""
    if schema_editor.connection.alias != "default":
        return
    from django.db import transaction

    Offer = apps.get_model("procurement", "Offer")
    Fitment = apps.get_model("procurement", "VehicleFitment")
    Listing = apps.get_model("procurement", "RetailListing")
    Activity = apps.get_model("procurement", "Activity")
    Supplier = apps.get_model("procurement", "Supplier")
    Task = apps.get_model("catalog", "SupplierPhotoTask")
    if not Supplier.objects.filter(pk=5, name="ZakazAuto — Костанай").exists():
        return

    counts = {"fitment_offers": 0, "make_model_pairs": 0, "photo_tasks": 0}
    with transaction.atomic():
        for row in reviewed_rows():
            offer = Offer.objects.select_for_update().filter(pk=row["offer_id"]).first()
            listing = Listing.objects.select_related("product").filter(
                offer_id=row["offer_id"], product_id=row["product_id"], enabled=True
            ).first()
            if not exact_offer(offer, row) or listing is None:
                continue

            if not Fitment.objects.filter(offer_id=offer.pk).exists():
                grouped = {}
                for fit in row["fitments"]:
                    config = {key: value for key, value in fit.items() if key not in ("make", "model")}
                    configs = grouped.setdefault((fit["make"], fit["model"]), [])
                    if config not in configs:
                        configs.append(config)
                for (make, model), configs in grouped.items():
                    Fitment.objects.create(
                        offer_id=offer.pk,
                        make=make,
                        model=model,
                        configurations=configs,
                        source_url=configs[0]["source_url"],
                        verified=True,
                    )
                meta = dict(offer.characteristics or {})
                meta["reviewed_applicability"] = {
                    "source_label": row["source_label"],
                    "source_kind": row["source_kind"],
                    "source_urls": row["source_urls"],
                    "checked_on": "2026-11-14",
                    "source_article": row["article"],
                    "source_brand": row["brand"],
                    "status": "sourced",
                    "note": row["fitment_note"],
                }
                offer.characteristics = meta
                offer.save(update_fields=["characteristics"])
                counts["fitment_offers"] += 1
                counts["make_model_pairs"] += len(grouped)

            product = listing.product
            if product.status != "active" or product.article != row["article"] or product.main_image:
                continue
            task = Task.objects.select_for_update().select_related("offer").filter(product_id=product.pk).first()
            if task and (
                task.pk != row["task_id"]
                or task.offer.brand != row["brand"]
                or task.offer.article != row["article"]
            ):
                continue
            if task is None:
                task = Task(pk=row["task_id"], product_id=product.pk, offer_id=offer.pk)
            task.offer_id = offer.pk
            task.source_url = row["image_source_url"]
            task.status = TASK_STATUS
            task.note = "Точное фото проверено и загружено; ожидает локального сохранения."
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": BATCH,
                "reviewed_manifest_sha256": MANIFEST_SHA256,
            }
            task.save()
            counts["photo_tasks"] += 1
        Activity.objects.create(
            user_id=None,
            action="reviewed_nightly_photo_fitment_import",
            details={"batch": BATCH, **counts},
        )


def photo_bytes(row, key):
    data = base64.b64decode(row[key + "_b64"], validate=True)
    if len(data) > 2 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != row[key + "_sha256"]:
        raise ValueError("Reviewed photograph checksum mismatch")
    return data


def process_reviewed_photos(limit=10):
    """Write packaged images through the existing sole photo consumer."""
    from django.conf import settings
    from django.db import transaction
    from django.utils import timezone
    from catalog.models import Product, SupplierPhotoTask
    from catalog.source_photos import save_shared_image
    from procurement.models import Offer, RetailListing

    manifest = {row["offer_id"]: row for row in reviewed_rows()}
    task_ids = list(
        SupplierPhotoTask.objects.filter(
            status=TASK_STATUS, evidence__reviewed_batch=BATCH
        ).order_by("pk").values_list("pk", flat=True)[:max(0, limit)]
    )
    done = 0
    for task_id in task_ids:
        with transaction.atomic():
            task = SupplierPhotoTask.objects.select_for_update(
                skip_locked=True, of=("self",)
            ).select_related("offer", "product").filter(pk=task_id, status=TASK_STATUS).first()
            if task is None:
                continue
            row = manifest.get(task.offer_id)
            listing = RetailListing.objects.filter(
                product_id=task.product_id, offer_id=task.offer_id, enabled=True
            ).first()
            product = Product.objects.select_for_update().get(pk=task.product_id)
            if (
                not row
                or not listing
                or task.pk != row["task_id"]
                or product.pk != row["product_id"]
                or not exact_offer(task.offer, row)
                or product.article != row["article"]
                or product.status != "active"
                or task.source_url != row["image_source_url"]
                or task.evidence.get("reviewed_manifest_sha256") != MANIFEST_SHA256
            ):
                task.status = "skipped"
                task.note = "Карточка изменилась; адресное добавление отменено."
            elif product.main_image:
                task.status = "skipped"
                task.note = "Существующая фотография сохранена."
            else:
                main, thumb = photo_bytes(row, "main"), photo_bytes(row, "thumbnail")
                media = Path(settings.MEDIA_ROOT)
                media.mkdir(parents=True, exist_ok=True)
                free = shutil.disk_usage(media).free
                if free < len(main) + len(thumb) + 64 * 1024 * 1024:
                    task.status = "blocked"
                    task.note = "Недостаточно свободного места; тариф не изменён."
                    task.evidence = {**task.evidence, "block_kind": "storage", "free_disk_bytes": free}
                else:
                    product.main_image.name = save_shared_image(product.main_image.storage, main)
                    product.supplier_thumbnail.name = save_shared_image(product.supplier_thumbnail.storage, thumb)
                    product.updated_at = timezone.now()
                    product.save(update_fields=["main_image", "supplier_thumbnail", "updated_at"])
                    Offer.objects.filter(pk=task.offer_id, photo_url__in=[None, "", row["image_url"]]).update(
                        photo_url=product.main_image.url
                    )
                    task.status = "done"
                    task.note = row["image_note"]
                    task.evidence = {
                        **task.evidence,
                        "source_url": row["image_source_url"],
                        "image_url": row["image_url"],
                        "brand": row["brand"],
                        "article": row["article"],
                        "source_sha256": row["source_sha256"],
                        "main_path": product.main_image.name,
                        "thumbnail_path": product.supplier_thumbnail.name,
                        "main_size": row["main_size"],
                        "thumbnail_size": row["thumbnail_size"],
                        "main_bytes": len(main),
                        "thumbnail_bytes": len(thumb),
                        "checked_at": timezone.now().isoformat(),
                    }
            task.save(update_fields=["status", "note", "evidence", "updated_at"])
            done += 1
            if task.status == "blocked":
                break
    return done
