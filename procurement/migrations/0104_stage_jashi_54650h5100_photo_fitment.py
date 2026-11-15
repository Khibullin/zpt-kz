from django.db import migrations


SOURCE_URL = "https://kaspi.kz/shop/p/jashi-perednii-54650h5100-1-sht-144183578/"
IMAGE_URL = "https://resources.cdn-kaspi.kz/img/m/p/p88/p76/58890227.png?format=gallery-medium"
OEM_CONFIRMATION_URL = "https://yoshiparts.com/parts/hyundai-54650H5100"
KZ_CONFIRMATION_URL = "https://jbparts.kz/part/amortizator-perednie-l-hyundai-accent-rio-1-4-1-6-17-22-koober2/70"


def stage_exact_jashi_photo_fitment(apps, schema_editor):
    """Stage one exact JASHI card and only the discrete fitment years it states."""
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
            pk=25289,
            supplier_id=5,
            brand="JASHI",
            article="54650H5100",
            stock="1",
            cost="14400.00",
            kaspi_price="22760.00",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=6957,
            article="54650H5100",
            status="active",
            main_image="",
            supplier_thumbnail="",
            price=18720,
            cost_price=14400,
            stock_qty=1,
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4930,
            product_id=6957,
            offer_id=25289,
            status="needs_source",
            source_url="",
        ).first()
        fitment_empty = not VehicleFitment.objects.filter(offer_id=25289).exists()

        if offer is not None and product is not None and task is not None and fitment_empty:
            common = {
                "years_source_kind": "discrete_years_on_exact_card",
                "placement": "front",
                "side": "left",
                "execution": "bare strut without spring",
                "oem_article": "54650H5100",
                "package_quantity": 1,
                "generation": None,
                "engine": None,
                "source_url": SOURCE_URL,
                "oem_confirmation_url": OEM_CONFIRMATION_URL,
                "kz_confirmation_url": KZ_CONFIRMATION_URL,
            }
            rows = (
                ("HYUNDAI", "Accent"),
                ("HYUNDAI", "Solaris"),
                ("KIA", "Rio"),
            )
            for make, model in rows:
                VehicleFitment.objects.create(
                    offer_id=25289,
                    make=make,
                    model=model,
                    details=(
                        "Передняя левая голая стойка без пружины; 1 шт.; "
                        "точная карточка отдельно перечисляет 2017–2025. "
                        "Поколение и двигатель источником не указаны."
                    ),
                    source_url=SOURCE_URL,
                    verified=True,
                    configurations=[{**common, "years": str(year)} for year in range(2017, 2026)],
                )
                fitment_added += 1

            task.source_url = SOURCE_URL
            task.status = "weekend15k_ready"
            task.note = (
                "Адресная публикация предметного фото и характеристик точной карточки Kaspi "
                "JASHI 54650H5100; годы сохранены отдельными значениями, без двигателей."
            )
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": "weekend-20261115-k",
                "source_kind": "exact_kaspi_card",
                "source_url": SOURCE_URL,
                "oem_confirmation_url": OEM_CONFIRMATION_URL,
                "kz_confirmation_url": KZ_CONFIRMATION_URL,
                "image_url": IMAGE_URL,
                "verified_image_sha256": "cde6c7ba0589258b38fffe912858fecbc633341d7c0a19a408fad67c093f38e1",
                "verified_image_size": "750x603",
                "verified_image_bytes": 47065,
                "brand": "JASHI",
                "article": "54650H5100",
                "source_article": "54650H5100",
                "execution": "front left bare strut without spring",
                "placement": "front",
                "side": "left",
                "package_quantity": 1,
                "fitment_models": ["HYUNDAI Accent", "HYUNDAI Solaris", "KIA Rio"],
                "fitment_years": [str(year) for year in range(2017, 2026)],
                "engines_confirmed": False,
                "generation_confirmed": False,
                "checked_on": "2026-11-15",
                "exact_card": True,
                "visual_check": "real isolated bare strut; no car, diagram, analogue or placeholder",
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            staged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_jashi_54650h5100_photo_fitment_staged",
            details={
                "batch": "weekend-20261115-k",
                "offer_id": 25289,
                "product_id": 6957,
                "task_id": 4930,
                "photo_tasks": staged,
                "fitment_rows_added": fitment_added,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0103_restage_jashi_54651_d3000_after_guard")]
    operations = [
        migrations.RunPython(stage_exact_jashi_photo_fitment, migrations.RunPython.noop),
    ]
