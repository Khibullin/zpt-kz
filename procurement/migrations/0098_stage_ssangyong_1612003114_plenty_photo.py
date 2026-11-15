from django.db import migrations


SOURCE_URL = (
    "https://plenty.parts/parts/ssangyong/engine/"
    "ssangyong-1612003114-vibration-damper-for-ssangyong-kg-mobility-p2259592"
)
IMAGE_URL = "https://i.plenty.parts/i/850/pictures_ppp_ssangyong_1612003114.jpg"


def stage_exact_ssangyong_photo(apps, schema_editor):
    """Stage one exact reviewed photo; preserve trade data and unresolved fitment."""
    if schema_editor.connection.alias != "default":
        return

    from django.db import transaction

    Task = apps.get_model("catalog", "SupplierPhotoTask")
    Product = apps.get_model("catalog", "Product")
    Offer = apps.get_model("procurement", "Offer")
    VehicleFitment = apps.get_model("procurement", "VehicleFitment")
    Activity = apps.get_model("procurement", "Activity")

    staged = 0
    with transaction.atomic():
        offer = Offer.objects.select_for_update().filter(
            pk=25081,
            supplier_id=5,
            brand="SsangYong",
            article="1612003114",
            stock="3",
            cost="11100.00",
            kaspi_price="20060.00",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=6827,
            article="1612003114",
            status="active",
            main_image="",
            supplier_thumbnail="",
            price=14430,
            cost_price=11100,
            stock_qty=3,
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4800,
            product_id=6827,
            offer_id=25081,
            status="needs_source",
            source_url="",
        ).first()
        # The exact Kaspi card and independent catalogue disagree on vehicle
        # coverage and start years. Keep structured fitment empty rather than
        # turning either source into an unsupported range.
        fitment_unchanged = not VehicleFitment.objects.filter(offer_id=25081).exists()
        if offer is not None and product is not None and task is not None and fitment_unchanged:
            task.source_url = SOURCE_URL
            task.status = "weekend15h_ready"
            task.note = (
                "Адресная публикация предметного фото точной карточки Plenty.Parts "
                "SsangYong 1612003114, демпфер поликлинового ремня. "
                "Применимость не записана из-за конфликта источников."
            )
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": "weekend-20261115-h",
                "source_kind": "exact_international_catalogue_card",
                "source_url": SOURCE_URL,
                "image_url": IMAGE_URL,
                "verified_image_sha256": "c4f312d7d6fedb6feb51b0bb1facebd3c1c307deb01a92fccdc7b010cff1391e",
                "verified_image_size": "454x342",
                "verified_image_bytes": 21396,
                "brand": "SsangYong",
                "article": "1612003114",
                "source_article": "1612003114",
                "execution": "vibration damper for V-ribbed belt",
                "package_quantity": None,
                "checked_on": "2026-11-15",
                "exact_card": True,
                "fitment_status": "not_saved_source_conflict",
                "visual_check": (
                    "real belt vibration damper with SsangYong packaging; "
                    "no car, diagram, analogue or placeholder"
                ),
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            staged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_ssangyong_1612003114_photo_staged",
            details={
                "batch": "weekend-20261115-h",
                "offer_id": 25081,
                "product_id": 6827,
                "task_id": 4800,
                "photo_tasks": staged,
                "fitment_rows_added": 0,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0097_stage_hyundai_kia_553102p500_partsouq_photo")]
    operations = [
        migrations.RunPython(stage_exact_ssangyong_photo, migrations.RunPython.noop),
    ]
