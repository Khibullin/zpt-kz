from django.db import migrations


SOURCE_URL = "https://shop.globalparts.kz/catalog/card/210534"
IMAGE_URL = "https://resources.cdn-kaspi.kz/img/m/p/p65/pe2/68673997.png?format=gallery-large"
CTR_URL = "https://aftermarket.ctr.co.kr/files/214f77492147bb699d6fb2030be19ec2.pdf"


def stage_exact_jashi_photo_fitment(apps, schema_editor):
    """Stage one exact photo and only source-confirmed fitment configurations."""
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
            pk=25213,
            supplier_id=5,
            brand="JASHI",
            article="54651-D3000",
            stock="2",
            cost="11800.00",
            kaspi_price="20000.00",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=6764,
            article="54651-D3000",
            status="active",
            main_image="",
            supplier_thumbnail="",
            price=15340,
            cost_price=11800,
            stock_qty=2,
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4737,
            product_id=6764,
            offer_id=25213,
            status="needs_source",
            source_url="",
        ).first()
        fitment_empty = not VehicleFitment.objects.filter(offer_id=25213).exists()

        if offer is not None and product is not None and task is not None and fitment_empty:
            common = {
                "placement": "front",
                "side": "left",
                "oem_article": "54651-D3000",
                "package_quantity": 1,
                "engine": None,
                "source_url": SOURCE_URL,
                "confirmation_url": CTR_URL,
            }
            VehicleFitment.objects.create(
                offer_id=25213,
                make="HYUNDAI",
                model="Tucson (TL)",
                details="Передняя левая стойка; OEM 54651-D3000; 1 шт. Двигатель не указан.",
                source_url=SOURCE_URL,
                verified=True,
                configurations=[{**common, "years": str(year)} for year in range(2015, 2022)],
            )
            VehicleFitment.objects.create(
                offer_id=25213,
                make="KIA",
                model="Sportage (QL)",
                details="Передняя левая стойка; OEM 54651-D3000; 1 шт. Двигатель не указан.",
                source_url=SOURCE_URL,
                verified=True,
                configurations=[{**common, "years": str(year)} for year in range(2016, 2022)],
            )
            fitment_added = 2

            task.source_url = SOURCE_URL
            task.status = "weekend15j_ready"
            task.note = (
                "Адресная публикация предметного фото точной карточки Global Parts KZ "
                "JASHI 54651-D3000 и подтверждённой применимости Tucson/Sportage."
            )
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": "weekend-20261115-j",
                "source_kind": "exact_kz_catalogue_card",
                "source_url": SOURCE_URL,
                "fitment_confirmation_url": CTR_URL,
                "image_url": IMAGE_URL,
                "verified_image_sha256": "b75b65ea9254a36b432e5b51c7111eec80c5a4ee222d3120b8d2cf6074bfc9c9",
                "verified_image_size": "483x322",
                "verified_image_bytes": 29638,
                "brand": "JASHI",
                "article": "54651-D3000",
                "source_article": "54651-D3000",
                "execution": "front left suspension strut",
                "placement": "front",
                "side": "left",
                "package_quantity": 1,
                "checked_on": "2026-11-15",
                "exact_card": True,
                "visual_check": "real isolated strut; no car, diagram, analogue or placeholder",
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            staged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_jashi_54651_d3000_photo_fitment_staged",
            details={
                "batch": "weekend-20261115-j",
                "offer_id": 25213,
                "product_id": 6764,
                "task_id": 4737,
                "photo_tasks": staged,
                "fitment_rows_added": fitment_added,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0100_add_jashi_c281f260203_1100_fitment")]
    operations = [
        migrations.RunPython(stage_exact_jashi_photo_fitment, migrations.RunPython.noop),
    ]
