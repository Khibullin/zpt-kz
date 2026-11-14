from django.db import migrations


SOURCE_URL = "https://www.ebay.com/itm/157310193004"
IMAGE_URL = "https://i.ebayimg.com/images/g/XLIAAeSwdGFowS-W/s-l1600.webp"
PREVIOUS_URL = "https://item.gmarket.co.kr/Item?goodsCode=4348280358"


def restage_exact_ssangyong_photo(apps, schema_editor):
    """Move one blocked task to a different exact single-unit public source."""
    if schema_editor.connection.alias != "default":
        return

    from django.db import transaction

    Task = apps.get_model("catalog", "SupplierPhotoTask")
    Product = apps.get_model("catalog", "Product")
    Offer = apps.get_model("procurement", "Offer")
    VehicleFitment = apps.get_model("procurement", "VehicleFitment")
    Activity = apps.get_model("procurement", "Activity")

    staged = 0
    with transaction.atomic():
        offer = Offer.objects.select_for_update().filter(
            pk=25114, supplier_id=5, brand="SsangYong",
            article="4530132100", stock="1", cost="18700.00",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=6829, article="4530132100", status="active",
            main_image="", supplier_thumbnail="", price=24310,
            cost_price=18700, stock_qty=1,
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4802, product_id=6829, offer_id=25114,
            status="blocked", source_url=PREVIOUS_URL,
        ).first()
        fitment_ok = VehicleFitment.objects.filter(
            offer_id=25114, make="SSANGYONG",
            model="Korando Sports (Q150)", verified=True,
        ).exists()
        if offer is not None and product is not None and task is not None and fitment_ok:
            task.source_url = SOURCE_URL
            task.status = "weekend15d_ready"
            task.note = (
                "Gmarket вернул HTTP 403 и не повторяется. Подготовлен другой "
                "точный источник SsangYong 4530132100, фото и цена за 1 шт."
            )
            task.evidence = {
                **(task.evidence or {}),
                "previous_source_attempts": [
                    {
                        "url": PREVIOUS_URL,
                        "result": "temporary_source_unavailable_http_403",
                        "checked_on": "2026-11-15",
                        "retry_policy": "do_not_retry_without_new_source_state",
                    }
                ],
                "block_kind": None,
                "reviewed_batch": "weekend-20261115-d",
                "source_kind": "checked_international_exact_listing",
                "source_url": SOURCE_URL,
                "image_url": IMAGE_URL,
                "brand": "SsangYong",
                "article": "4530132100",
                "formatted_oem": "45301-32100",
                "execution": "rear gas shock absorber",
                "placement": "rear",
                "side": "left_or_right",
                "package_quantity": 1,
                "checked_on": "2026-11-15",
                "exact_catalogue_page": True,
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            staged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_ssangyong_4530132100_photo_restaged",
            details={
                "batch": "weekend-20261115-d",
                "offer_id": 25114,
                "product_id": 6829,
                "task_id": 4802,
                "photo_tasks": staged,
                "previous_source_result": "temporary_source_unavailable_http_403",
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0093_stage_ssangyong_4530132100_gmarket_photo")]
    operations = [
        migrations.RunPython(restage_exact_ssangyong_photo, migrations.RunPython.noop),
    ]
