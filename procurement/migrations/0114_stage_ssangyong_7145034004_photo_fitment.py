from django.db import migrations


SOURCE_URL = "https://www.autoopt.ru/catalog/912907-amortizator_ssangyong_actyon_10__kryshki_bagazhnika_levyj_oe"
IMAGE_URL = "https://www.autoopt.ru/product_pictures/big/0fe/912907.jpg"
SECONDARY_FITMENT_URL = "https://koreanaparts.ru/catalog/ssang-yong/actyon-new-korando-c-c200-2011"


def stage_exact_ssangyong_7145034004(apps, schema_editor):
    """Stage the exact left tailgate strut photo and bounded fitment."""
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
            pk=25085,
            supplier_id=5,
            brand="SsangYong",
            article="7145034004",
            stock="1",
            cost="10900.00",
            kaspi_price="17960.00",
            photo_url="",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=6832,
            article="7145034004",
            status="active",
            main_image="",
            supplier_thumbnail="",
            price=14170,
            cost_price=10900,
            stock_qty=1,
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4805,
            product_id=6832,
            offer_id=25085,
            status="needs_source",
            source_url="",
        ).first()
        fitment_empty = not VehicleFitment.objects.filter(offer_id=25085).exists()

        if offer is not None and product is not None and task is not None and fitment_empty:
            VehicleFitment.objects.create(
                offer_id=25085,
                make="SSANGYONG",
                model="Actyon / New Actyon",
                details=(
                    "Точный каталог SSANGYONG 7145034004 подтверждает левый упор крышки "
                    "багажника для Actyon от 2010 года. Конечный год, поколение и двигатели "
                    "источником не указаны и не добавлены."
                ),
                source_url=SOURCE_URL,
                verified=True,
                configurations=[
                    {
                        "years": "2010–",
                        "year_from": 2010,
                        "year_to": None,
                        "placement": "tailgate left",
                        "execution": "gas strut",
                        "engine": None,
                    },
                ],
            )
            fitment_added = 1
            task.source_url = SOURCE_URL
            task.status = "weekend15s_ready"
            task.note = (
                "Адресная публикация предметного фото точного SSANGYONG 7145034004; "
                "применимость ограничена левым упором крышки багажника Actyon от 2010 года."
            )
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": "weekend-20261115-s",
                "source_kind": "exact_checked_catalogue_card",
                "source_url": SOURCE_URL,
                "fitment_source_urls": [SOURCE_URL, SECONDARY_FITMENT_URL],
                "image_url": IMAGE_URL,
                "verified_image_size": "922x768",
                "verified_image_sha256": "f3748cf1663c711d9ec25b01031a0400df5d3bf2614dbd5f5e460a9a060baf07",
                "brand": "SsangYong",
                "source_brand": "SSANGYONG",
                "article": "7145034004",
                "execution": "left tailgate gas strut",
                "package_quantity": 1,
                "fitment_models": ["SSANGYONG Actyon / New Actyon"],
                "fitment_years": ["2010–"],
                "fitment_engines": [],
                "checked_on": "2026-11-15",
                "exact_card": True,
                "visual_check": "real isolated black gas strut; no car, diagram, analogue or placeholder",
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            staged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_ssangyong_7145034004_photo_fitment_staged",
            details={
                "batch": "weekend-20261115-s",
                "offer_id": 25085,
                "product_id": 6832,
                "task_id": 4805,
                "photo_tasks": staged,
                "fitment_rows_added": fitment_added,
                "trade_data_changed": False,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0113_stage_ssangyong_7146034004_photo_fitment")]
    operations = [
        migrations.RunPython(stage_exact_ssangyong_7145034004, migrations.RunPython.noop),
    ]
