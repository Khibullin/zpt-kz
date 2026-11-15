from django.db import migrations


SOURCE_URL = "https://kaspi.kz/shop/p/jashi-perednii-54660h5100-1-sht-144184316/"
IMAGE_URL = "https://resources.cdn-kaspi.kz/img/m/p/ped/pc6/58893082.png?format=gallery-medium"
OEM_CONFIRMATION_URL = "https://yoshiparts.com/parts/hyundai-54660H5100"
CATALOGUE_CONFIRMATION_URL = "https://partsouq.com/en/search/all?q=54660H5100"


def stage_exact_jashi_photo_fitment(apps, schema_editor):
    """Stage one exact right-hand JASHI card and only its discrete fitment years."""
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
            pk=25290,
            supplier_id=5,
            brand="JASHI",
            article="54660H5100",
            stock="5",
            cost="14400.00",
            kaspi_price="22760.00",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=6961,
            article="54660H5100",
            status="active",
            main_image="",
            supplier_thumbnail="",
            price=18720,
            cost_price=14400,
            stock_qty=5,
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4934,
            product_id=6961,
            offer_id=25290,
            status="needs_source",
            source_url="",
        ).first()
        fitment_empty = not VehicleFitment.objects.filter(offer_id=25290).exists()

        if offer is not None and product is not None and task is not None and fitment_empty:
            common = {
                "years_source_kind": "discrete_years_on_exact_card",
                "placement": "front",
                "side": "right",
                "execution": "bare strut without spring",
                "oem_article": "54660H5100",
                "package_quantity": 1,
                "generation": None,
                "engine": None,
                "source_url": SOURCE_URL,
                "oem_confirmation_url": OEM_CONFIRMATION_URL,
                "catalogue_confirmation_url": CATALOGUE_CONFIRMATION_URL,
            }
            rows = (
                ("HYUNDAI", "Accent"),
                ("HYUNDAI", "Solaris"),
                ("KIA", "Rio"),
            )
            for make, model in rows:
                VehicleFitment.objects.create(
                    offer_id=25290,
                    make=make,
                    model=model,
                    details=(
                        "Передняя правая голая стойка без пружины; 1 шт.; "
                        "точная карточка отдельно перечисляет 2017–2025. "
                        "Поколение и двигатель источником не указаны."
                    ),
                    source_url=SOURCE_URL,
                    verified=True,
                    configurations=[{**common, "years": str(year)} for year in range(2017, 2026)],
                )
                fitment_added += 1

            task.source_url = SOURCE_URL
            task.status = "weekend15l_ready"
            task.note = (
                "Адресная публикация предметного фото и характеристик точной карточки Kaspi "
                "JASHI 54660H5100; годы сохранены отдельными значениями, без двигателей."
            )
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": "weekend-20261115-l",
                "source_kind": "exact_kaspi_card",
                "source_url": SOURCE_URL,
                "oem_confirmation_url": OEM_CONFIRMATION_URL,
                "catalogue_confirmation_url": CATALOGUE_CONFIRMATION_URL,
                "image_url": IMAGE_URL,
                "verified_image_sha256": "8bb584b6700964c3f43056682be114064b98a7958014f5630ee7a9aefd610e4e",
                "verified_image_size": "571x420",
                "verified_image_bytes": 32230,
                "brand": "JASHI",
                "article": "54660H5100",
                "source_article": "54660H5100",
                "execution": "front right bare strut without spring",
                "placement": "front",
                "side": "right",
                "package_quantity": 1,
                "fitment_models": ["HYUNDAI Accent", "HYUNDAI Solaris", "KIA Rio"],
                "fitment_years": [str(year) for year in range(2017, 2026)],
                "engines_confirmed": False,
                "generation_confirmed": False,
                "checked_on": "2026-11-15",
                "exact_card": True,
                "visual_check": "real isolated bare right strut; no car, diagram, analogue or placeholder",
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            staged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_jashi_54660h5100_photo_fitment_staged",
            details={
                "batch": "weekend-20261115-l",
                "offer_id": 25290,
                "product_id": 6961,
                "task_id": 4934,
                "photo_tasks": staged,
                "fitment_rows_added": fitment_added,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0104_stage_jashi_54650h5100_photo_fitment")]
    operations = [
        migrations.RunPython(stage_exact_jashi_photo_fitment, migrations.RunPython.noop),
    ]
