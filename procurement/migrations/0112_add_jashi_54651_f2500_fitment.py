from django.db import migrations


FITMENT_SOURCE_URL = "https://aftermarket.ctr.co.kr/files/214f77492147bb699d6fb2030be19ec2.pdf"
GLOBAL_PARTS_SEARCH_URL = "https://shop.globalparts.kz/getCatalog?partNumber=54651F2500"
CONFLICTING_KASPI_URL = "https://kaspi.kz/shop/p/expert-perednii-54651-f2500-1-sht-119619624/"


def add_exact_jashi_54651_f2500_fitment(apps, schema_editor):
    """Add only the fitment confirmed by the official CTR OEM cross catalogue."""
    if schema_editor.connection.alias != "default":
        return

    from django.db import transaction

    Task = apps.get_model("catalog", "SupplierPhotoTask")
    Product = apps.get_model("catalog", "Product")
    Offer = apps.get_model("procurement", "Offer")
    VehicleFitment = apps.get_model("procurement", "VehicleFitment")
    Activity = apps.get_model("procurement", "Activity")

    fitment_added = 0
    with transaction.atomic():
        offer = Offer.objects.select_for_update().filter(
            pk=25206,
            supplier_id=5,
            brand="JASHI",
            article="54651-F2500",
            stock="1",
            cost="10000.00",
            kaspi_price="16997.00",
            photo_url="",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=6766,
            article="54651-F2500",
            status="active",
            main_image="",
            supplier_thumbnail="",
            price=13000,
            cost_price=10000,
            stock_qty=1,
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4739,
            product_id=6766,
            offer_id=25206,
            status="needs_source",
            source_url="",
        ).first()
        fitment_empty = not VehicleFitment.objects.filter(offer_id=25206).exists()

        if offer is not None and product is not None and task is not None and fitment_empty:
            VehicleFitment.objects.create(
                offer_id=25206,
                make="HYUNDAI",
                model="Avante / Elantra (AD)",
                details=(
                    "Официальный каталог CTR связывает OEM 54651-F2500 с Hyundai "
                    "Avante AD / Elantra AD, 2015–2020, передняя левая стойка. "
                    "Двигатели источником не указаны; иные модели не добавлены."
                ),
                source_url=FITMENT_SOURCE_URL,
                verified=True,
                configurations=[
                    {
                        "generation": "AD",
                        "years": "2015–2020",
                        "year_from": 2015,
                        "year_to": 2020,
                        "placement": "front left",
                        "execution": "suspension strut",
                        "engine": None,
                    },
                ],
            )
            fitment_added = 1
            task.note = (
                "Применимость сохранена по официальному OEM-кроссу CTR. Точное фото JASHI "
                "54651-F2500 не найдено: доступные точные карточки имеют бренды Expert или Hyundai/Kia."
            )
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": "weekend-20261115-q",
                "source_kind": "official_ctr_oem_cross_fitment_only",
                "fitment_source_urls": [FITMENT_SOURCE_URL],
                "checked_photo_sources": [GLOBAL_PARTS_SEARCH_URL, CONFLICTING_KASPI_URL],
                "photo_result": "unconfirmed_brand_conflict_no_write",
                "brand": "JASHI",
                "article": "54651-F2500",
                "execution": "front left suspension strut",
                "fitment_models": ["HYUNDAI Avante / Elantra (AD)"],
                "fitment_years": [2015, 2016, 2017, 2018, 2019, 2020],
                "fitment_engines": [],
                "checked_on": "2026-11-15",
                "retry_condition": "new exact JASHI product-photo source only",
            }
            task.save(update_fields=["note", "evidence", "updated_at"])

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_jashi_54651_f2500_fitment_added_photo_unconfirmed",
            details={
                "batch": "weekend-20261115-q",
                "offer_id": 25206,
                "product_id": 6766,
                "task_id": 4739,
                "fitment_rows_added": fitment_added,
                "photo_tasks_staged": 0,
                "photo_reason": "exact JASHI photo not found; conflicting brands rejected",
                "trade_data_changed": False,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0111_stage_parts_mall_pqb007_photo_fitment")]
    operations = [
        migrations.RunPython(add_exact_jashi_54651_f2500_fitment, migrations.RunPython.noop),
    ]
