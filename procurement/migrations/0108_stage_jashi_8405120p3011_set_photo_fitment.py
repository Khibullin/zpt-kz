from django.db import migrations


SOURCE_URL = "https://kaspi.kz/shop/p/jashi-kreplenie-8405120p3011-jac-t6-120901371/"
IMAGE_URL = "https://resources.cdn-kaspi.kz/img/m/p/hcb/h6e/86407480410142.jpg?format=gallery-large"
CATALOGUE_URL = "https://service-forward.ru/%D0%B7%D0%B0%D0%BF%D1%87%D0%B0%D1%81%D1%82%D0%B8/%D0%B7%D0%B0%D0%BF%D1%87%D0%B0%D1%81%D1%82%D0%B8-%D0%B4%D0%BB%D1%8F-%D0%B3%D1%80%D1%83%D0%B7%D0%BE%D0%B2%D0%B8%D0%BA%D0%BE%D0%B2-jac/%D0%BA%D0%B0%D1%82%D0%B0%D0%BB%D0%BE%D0%B3-%D0%BB%D0%B5%D0%B3%D0%BA%D0%BE%D0%B2%D1%8B%D1%85-%D0%B7%D0%B0%D0%BF%D1%87%D0%B0%D1%81%D1%82%D0%B5%D0%B9-jac/"


def stage_exact_jashi_step_set(apps, schema_editor):
    """Stage the exact six-piece JAC T6 step-bracket set and conservative fitment."""
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
            pk=28110,
            supplier_id=5,
            brand="JASHI",
            article="8405120P3011/SET/6p/",
            stock="2",
            cost="17400.00",
            kaspi_price="26060.00",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=9237,
            article="8405120P3011/SET/6p/",
            status="active",
            main_image="",
            supplier_thumbnail="",
            price=22620,
            cost_price=17400,
            stock_qty=2,
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=7210,
            product_id=9237,
            offer_id=28110,
            status="needs_source",
            source_url="",
        ).first()
        fitment_empty = not VehicleFitment.objects.filter(offer_id=28110).exists()

        if offer is not None and product is not None and task is not None and fitment_empty:
            part_numbers = [
                "8405120P3011",
                "8405130P3011",
                "8405140P3011",
                "8405220P3011",
                "8405230P3011",
                "8405240P3011",
            ]
            VehicleFitment.objects.create(
                offer_id=28110,
                make="JAC",
                model="T6",
                details=(
                    "Комплект из 6 кронштейнов крепления левой и правой подножек. "
                    "Merchant SKU продавца: 8405120P3011/SET/6p/. Годы, поколение "
                    "и двигатели источниками не указаны."
                ),
                source_url=SOURCE_URL,
                verified=True,
                configurations=[{
                    "generation": None,
                    "years": None,
                    "engine": None,
                    "execution": "six-piece left/right step mounting bracket set",
                    "package_quantity": 6,
                    "merchant_sku": "8405120P3011/SET/6p/",
                    "component_articles": part_numbers,
                    "source_url": SOURCE_URL,
                    "catalogue_url": CATALOGUE_URL,
                }],
            )
            fitment_added = 1

            task.source_url = SOURCE_URL
            task.status = "weekend15n_ready"
            task.note = (
                "Адресная публикация фото точного комплекта JASHI 8405120P3011/SET/6p/; "
                "применимость ограничена подтверждённой моделью JAC T6."
            )
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": "weekend-20261115-n",
                "source_kind": "exact_kaspi_card_and_seller_merchant_sku",
                "source_url": SOURCE_URL,
                "catalogue_url": CATALOGUE_URL,
                "image_url": IMAGE_URL,
                "verified_image_size": "263x160",
                "brand": "JASHI",
                "article": "8405120P3011/SET/6p/",
                "source_article": "8405120P3011",
                "merchant_sku": "8405120P3011/SET/6p/",
                "execution": "six-piece left/right step mounting bracket set",
                "package_quantity": 6,
                "component_articles": part_numbers,
                "fitment_models": ["JAC T6"],
                "years_confirmed": False,
                "engines_confirmed": False,
                "generation_confirmed": False,
                "checked_on": "2026-11-15",
                "exact_card": True,
                "visual_check": (
                    "real isolated set of six black metal brackets; no car, "
                    "diagram, analogue or placeholder"
                ),
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            staged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_jashi_8405120p3011_set_photo_fitment_staged",
            details={
                "batch": "weekend-20261115-n",
                "offer_id": 28110,
                "product_id": 9237,
                "task_id": 7210,
                "photo_tasks": staged,
                "fitment_rows_added": fitment_added,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0107_display_fenox_a12254_fitment_periods")]
    operations = [
        migrations.RunPython(stage_exact_jashi_step_set, migrations.RunPython.noop),
    ]
