from django.db import migrations


SOURCE_URL = "https://www.autoopt.ru/catalog/912908-amortizator_ssangyong_actyon_10__kryshki_bagazhnika_pravyj_oe"
IMAGE_URL = "https://www.autoopt.ru/product_pictures/big/734/912908.jpg"
SECONDARY_FITMENT_URL = "https://koreanaparts.ru/catalog/ssang-yong/actyon-new-korando-c-c200-2011"


def stage_exact_ssangyong_7146034004(apps, schema_editor):
    """Stage the exact right tailgate strut photo and bounded fitment."""
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
            pk=25086,
            supplier_id=5,
            brand="SsangYong",
            article="7146034004",
            stock="1",
            cost="10800.00",
            kaspi_price="17860.00",
            photo_url="",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=6834,
            article="7146034004",
            status="active",
            main_image="",
            supplier_thumbnail="",
            price=14040,
            cost_price=10800,
            stock_qty=1,
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4807,
            product_id=6834,
            offer_id=25086,
            status="needs_source",
            source_url="",
        ).first()
        fitment_empty = not VehicleFitment.objects.filter(offer_id=25086).exists()

        if offer is not None and product is not None and task is not None and fitment_empty:
            VehicleFitment.objects.create(
                offer_id=25086,
                make="SSANGYONG",
                model="Actyon / New Actyon",
                details=(
                    "Точный каталог SSANGYONG 7146034004 подтверждает правый упор крышки "
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
                        "placement": "tailgate right",
                        "execution": "gas strut",
                        "engine": None,
                    },
                ],
            )
            fitment_added = 1
            task.source_url = SOURCE_URL
            task.status = "weekend15r_ready"
            task.note = (
                "Адресная публикация предметного фото точного SSANGYONG 7146034004; "
                "применимость ограничена правым упором крышки багажника Actyon от 2010 года."
            )
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": "weekend-20261115-r",
                "source_kind": "exact_checked_catalogue_card",
                "source_url": SOURCE_URL,
                "fitment_source_urls": [SOURCE_URL, SECONDARY_FITMENT_URL],
                "image_url": IMAGE_URL,
                "verified_image_size": "916x768",
                "verified_image_sha256": "dc00b266a7e6a6aaa2ca502e861fc4d3441966588e80002de66172c917870a78",
                "brand": "SsangYong",
                "source_brand": "SSANGYONG",
                "article": "7146034004",
                "execution": "right tailgate gas strut",
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
            action="reviewed_weekend_ssangyong_7146034004_photo_fitment_staged",
            details={
                "batch": "weekend-20261115-r",
                "offer_id": 25086,
                "product_id": 6834,
                "task_id": 4807,
                "photo_tasks": staged,
                "fitment_rows_added": fitment_added,
                "trade_data_changed": False,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0112_add_jashi_54651_f2500_fitment")]
    operations = [
        migrations.RunPython(stage_exact_ssangyong_7146034004, migrations.RunPython.noop),
    ]
