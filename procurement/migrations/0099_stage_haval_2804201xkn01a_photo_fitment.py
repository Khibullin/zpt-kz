from django.db import migrations


SOURCE_URL = "https://www.shindary.com/bumper/rear-bumper-2804201xkn01a-for-haval-h6.html"
IMAGE_URL = "https://www.shindary.com/data/upload/20240523/2804201XKN01A.jpg"
FITMENT_URL = "https://www.eliteparts.org/products/original-haval-h6-whole-rear-bumper"


def stage_exact_haval_photo_fitment(apps, schema_editor):
    """Stage one exact photo and narrow confirmed fitment; preserve trade data."""
    if schema_editor.connection.alias != "default":
        return

    from django.db import transaction

    Task = apps.get_model("catalog", "SupplierPhotoTask")
    Product = apps.get_model("catalog", "Product")
    Offer = apps.get_model("procurement", "Offer")
    VehicleFitment = apps.get_model("procurement", "VehicleFitment")
    Activity = apps.get_model("procurement", "Activity")

    staged = 0
    fitment_added = 0
    with transaction.atomic():
        offer = Offer.objects.select_for_update().filter(
            pk=25373,
            supplier_id=5,
            brand="HAVAL",
            article="2804201XKN01A",
            stock="1",
            cost="25190.00",
            kaspi_price="36160.00",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=6908,
            article="2804201XKN01A",
            status="active",
            main_image="",
            supplier_thumbnail="",
            price=32747,
            cost_price=25190,
            stock_qty=1,
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4881,
            product_id=6908,
            offer_id=25373,
            status="needs_source",
            source_url="",
        ).first()
        fitment_unchanged = not VehicleFitment.objects.filter(offer_id=25373).exists()
        if offer is not None and product is not None and task is not None and fitment_unchanged:
            VehicleFitment.objects.create(
                offer_id=25373,
                make="HAVAL",
                model="H6 (3rd generation)",
                details=(
                    "Задний бампер в сборе; оригинальный чёрный, без окраски. "
                    "Только комплектации P1/P2/P3; для P4 источник указывает OEM 2804110XKN01A."
                ),
                source_url=FITMENT_URL,
                verified=True,
                configurations=[{
                    "generation": "3rd generation",
                    "years": "2022–present",
                    "placement": "rear",
                    "part_type": "rear bumper assembly",
                    "finish": "original factory black, unpainted",
                    "oem_article": "2804201XKN01A",
                    "restriction": "P1/P2/P3 only; P4 uses 2804110XKN01A",
                    "source_url": FITMENT_URL,
                    "confirmation_url": SOURCE_URL,
                }],
            )
            fitment_added = 1
            task.source_url = SOURCE_URL
            task.status = "weekend15i_ready"
            task.note = (
                "Адресная публикация предметного фото точной карточки Shindary "
                "HAVAL 2804201XKN01A и узкой подтверждённой применимости H6 III."
            )
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": "weekend-20261115-i",
                "source_kind": "exact_international_catalogue_card",
                "source_url": SOURCE_URL,
                "fitment_source_url": FITMENT_URL,
                "image_url": IMAGE_URL,
                "verified_image_sha256": "393d76bd812e5dcc9f086c8a4001cdfd2216bed2ce5990bb1a791f613fa92dab",
                "verified_image_size": "600x600",
                "verified_image_bytes": 21558,
                "brand": "HAVAL",
                "article": "2804201XKN01A",
                "source_article": "2804201XKN01A",
                "execution": "rear bumper assembly",
                "placement": "rear",
                "package_quantity": 1,
                "checked_on": "2026-11-15",
                "exact_card": True,
                "visual_check": "real isolated rear bumper; no car, diagram, analogue or placeholder",
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            staged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_haval_2804201xkn01a_photo_fitment_staged",
            details={
                "batch": "weekend-20261115-i",
                "offer_id": 25373,
                "product_id": 6908,
                "task_id": 4881,
                "photo_tasks": staged,
                "fitment_rows_added": fitment_added,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0098_stage_ssangyong_1612003114_plenty_photo")]
    operations = [
        migrations.RunPython(stage_exact_haval_photo_fitment, migrations.RunPython.noop),
    ]
