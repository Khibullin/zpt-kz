from django.db import migrations


PHOTO_SOURCE_URL = "https://armtek.by/product/amortizator-maslyanyy-fenox-a12254-zadnyaya-os-sleva-zadnyaya-os-sprava-4363063"
IMAGE_URL = "https://img.armtek.ru/img/article/436/4363063/500x500/4363063_0.webp"
FITMENT_SOURCE_URL = "https://cartex.kz/part/a12254-fenox"
FITMENT_CONFIRMATION_URL = "https://auto-parts78.ru/catalog/fenox/a12254"


def stage_exact_fenox_photo_fitment(apps, schema_editor):
    """Stage one exact FENOX A12254 photo and source-stated fitment periods."""
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
            pk=25142,
            supplier_id=5,
            brand="Fenox",
            article="A12254",
            stock="1",
            cost="5500.00",
            kaspi_price="12585.00",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=6698,
            article="A12254",
            status="active",
            main_image="",
            supplier_thumbnail="",
            price=7150,
            cost_price=5500,
            stock_qty=1,
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4671,
            product_id=6698,
            offer_id=25142,
            status="needs_source",
            source_url="",
        ).first()
        fitment_empty = not VehicleFitment.objects.filter(offer_id=25142).exists()

        if offer is not None and product is not None and task is not None and fitment_empty:
            common = {
                "period_source_kind": "open_ended_period_on_exact_catalogue_card",
                "placement": "rear axle",
                "side": "left or right",
                "execution": "oil-pressure twin-tube shock absorber",
                "package_quantity": 1,
                "generation": "M100, M150 / KLYA",
                "source_url": FITMENT_SOURCE_URL,
                "confirmation_url": FITMENT_CONFIRMATION_URL,
            }
            configurations = [
                {
                    **common,
                    "engine": "F8CV 0.8",
                    "period": "09.1998–present",
                    "year_from": 1998,
                    "month_from": 9,
                    "year_to": None,
                },
                {
                    **common,
                    "engine": "B10S 1.0",
                    "period": "01.2003–present",
                    "year_from": 2003,
                    "month_from": 1,
                    "year_to": None,
                },
            ]
            VehicleFitment.objects.create(
                offer_id=25142,
                make="DAEWOO",
                model="Matiz",
                details=(
                    "M100/M150 (KLYA); задняя ось слева или справа; масляный "
                    "двухтрубный амортизатор; 1 шт. Периоды источника сохранены "
                    "открытыми: F8CV 0.8 с 09.1998, B10S 1.0 с 01.2003."
                ),
                source_url=FITMENT_SOURCE_URL,
                verified=True,
                configurations=configurations,
            )
            fitment_added = 1

            task.source_url = PHOTO_SOURCE_URL
            task.status = "weekend15m_ready"
            task.note = (
                "Адресная публикация предметного фото точного FENOX A12254; "
                "применимость и двигатели подтверждены двумя открытыми каталогами."
            )
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": "weekend-20261115-m",
                "source_kind": "exact_catalogue_card",
                "source_url": PHOTO_SOURCE_URL,
                "fitment_source_url": FITMENT_SOURCE_URL,
                "fitment_confirmation_url": FITMENT_CONFIRMATION_URL,
                "image_url": IMAGE_URL,
                "verified_image_size": "500x500",
                "brand": "FENOX",
                "article": "A12254",
                "source_article": "A12254",
                "execution": "rear oil-pressure twin-tube shock absorber",
                "placement": "rear axle",
                "side": "left or right",
                "package_quantity": 1,
                "oem_cross_numbers": ["96316781", "96342033", "96610023"],
                "fitment_models": ["DAEWOO Matiz M100/M150 (KLYA)"],
                "fitment_periods": ["09.1998–present", "01.2003–present"],
                "fitment_engines": ["F8CV 0.8", "B10S 1.0"],
                "checked_on": "2026-11-15",
                "exact_card": True,
                "visual_check": (
                    "real isolated FENOX shock absorber with product label; "
                    "no car, diagram, analogue or placeholder"
                ),
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            staged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_fenox_a12254_photo_fitment_staged",
            details={
                "batch": "weekend-20261115-m",
                "offer_id": 25142,
                "product_id": 6698,
                "task_id": 4671,
                "photo_tasks": staged,
                "fitment_rows_added": fitment_added,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0105_stage_jashi_54660h5100_photo_fitment")]
    operations = [
        migrations.RunPython(stage_exact_fenox_photo_fitment, migrations.RunPython.noop),
    ]
