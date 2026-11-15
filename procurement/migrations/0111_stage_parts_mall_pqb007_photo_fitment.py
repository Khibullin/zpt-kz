from django.db import migrations


PHOTO_SOURCE_URL = "https://kaspi.kz/shop/p/pmc-parts-mall-pqb-007-upor-kapota-dlja-kia-sorento-2002-2005-chernyi-179355295/"
IMAGE_URL = "https://resources.cdn-kaspi.kz/img/m/p/p1f/pa6/187024947.jpg?format=gallery-medium"
FITMENT_SOURCE_URL = "https://file.euroauto.ru/file/catalogs/firms/5011.pdf"
BRAND_SOURCE_URL = "https://www.parts-mall.com/brand"


def stage_exact_parts_mall_pqb007(apps, schema_editor):
    """Stage the exact PQB-007 image and manufacturer-catalogue fitment."""
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
            pk=25159,
            supplier_id=5,
            brand="Parts-Mall",
            article="pqb-007",
            stock="1",
            cost="1900.00",
            kaspi_price="7930.00",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=6813,
            article="pqb-007",
            status="active",
            main_image="",
            supplier_thumbnail="",
            price=2470,
            cost_price=1900,
            stock_qty=1,
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4786,
            product_id=6813,
            offer_id=25159,
            status="needs_source",
            source_url="",
        ).first()
        fitment_empty = not VehicleFitment.objects.filter(offer_id=25159).exists()

        if offer is not None and product is not None and task is not None and fitment_empty:
            configurations = [
                {
                    "generation": "BL",
                    "period": "01.2002–03.2006",
                    "years": "2002–2006",
                    "year_from": 2002,
                    "month_from": 1,
                    "year_to": 2006,
                    "month_to": 3,
                    "engine": "D4CB 2.5L",
                    "fuel": "diesel",
                    "execution": "hood lifter",
                },
                {
                    "generation": "BL",
                    "period": "01.2002–03.2006",
                    "years": "2002–2006",
                    "year_from": 2002,
                    "month_from": 1,
                    "year_to": 2006,
                    "month_to": 3,
                    "engine": "G6CU 3.5L",
                    "fuel": "gasoline",
                    "execution": "gas lifter - hood",
                },
            ]
            VehicleFitment.objects.create(
                offer_id=25159,
                make="KIA",
                model="Sorento (BL)",
                details=(
                    "Каталог производителя Parts-Mall подтверждает PQB-007 как упор капота "
                    "для Kia Sorento (BL) в период 01.2002–03.2006: D4CB 2.5L diesel "
                    "и G6CU 3.5L gasoline. Иные годы и двигатели не добавлены."
                ),
                source_url=FITMENT_SOURCE_URL,
                verified=True,
                configurations=configurations,
            )
            fitment_added = 1

            task.source_url = PHOTO_SOURCE_URL
            task.status = "weekend15p_ready"
            task.note = (
                "Адресная публикация предметного фото точного PMC/Parts-Mall PQB-007; "
                "применимость ограничена подтверждёнными строками каталога производителя."
            )
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": "weekend-20261115-p",
                "source_kind": "exact_kaspi_card_and_manufacturer_catalogue",
                "source_url": PHOTO_SOURCE_URL,
                "brand_source_url": BRAND_SOURCE_URL,
                "fitment_source_urls": [FITMENT_SOURCE_URL],
                "image_url": IMAGE_URL,
                "verified_image_size": "750x491",
                "verified_image_sha256": "35d970accf69a516de64f1b77333d4139c844aa5e9da6e3b68e82504900a2fd6",
                "brand": "Parts-Mall",
                "source_brand": "PMC / Parts-Mall",
                "article": "PQB-007",
                "execution": "hood lifter",
                "fitment_models": ["KIA Sorento (BL)"],
                "fitment_periods": ["01.2002–03.2006"],
                "fitment_engines": ["D4CB 2.5L diesel", "G6CU 3.5L gasoline"],
                "checked_on": "2026-11-15",
                "exact_card": True,
                "visual_check": (
                    "real isolated gas strut in packaging; no car, diagram, analogue or placeholder"
                ),
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            staged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_parts_mall_pqb007_photo_fitment_staged",
            details={
                "batch": "weekend-20261115-p",
                "offer_id": 25159,
                "product_id": 6813,
                "task_id": 4786,
                "photo_tasks": staged,
                "fitment_rows_added": fitment_added,
                "trade_data_changed": False,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0110_stage_hyundai_kia_55100h5600_photo_fitment")]
    operations = [
        migrations.RunPython(stage_exact_parts_mall_pqb007, migrations.RunPython.noop),
    ]
