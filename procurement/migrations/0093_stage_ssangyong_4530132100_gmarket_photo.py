from django.db import migrations


SOURCE_URL = "https://item.gmarket.co.kr/Item?goodsCode=4348280358"
IMAGE_URL = "https://gdimg.gmarket.co.kr/4348280358/still/600?ver=1741740426"


def stage_exact_ssangyong_photo(apps, schema_editor):
    """Stage one exact single-unit OEM photograph with full identity guards."""
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
            pk=25114,
            supplier_id=5,
            brand="SsangYong",
            article="4530132100",
            stock="1",
            cost="18700.00",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=6829,
            article="4530132100",
            status="active",
            main_image="",
            supplier_thumbnail="",
            price=24310,
            cost_price=18700,
            stock_qty=1,
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4802,
            product_id=6829,
            offer_id=25114,
            status="needs_source",
            source_url="",
        ).first()
        fitment_ok = VehicleFitment.objects.filter(
            offer_id=25114,
            make="SSANGYONG",
            model="Korando Sports (Q150)",
            verified=True,
        ).exists()
        if offer is not None and product is not None and task is not None and fitment_ok:
            task.source_url = SOURCE_URL
            # The currently live consumer does not activate this state. The new
            # release activates it only after the strict Gmarket parser is live.
            task.status = "weekend15c_ready"
            task.note = (
                "Подтверждён точный SsangYong 4530132100: карточка и изображение "
                "одного амортизатора, количество 1 шт."
            )
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": "weekend-20261115-c",
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
                "previous_conflict": "pair_presentations_not_used",
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            staged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_ssangyong_4530132100_photo_staged",
            details={
                "batch": "weekend-20261115-c",
                "offer_id": 25114,
                "product_id": 6829,
                "task_id": 4802,
                "photo_tasks": staged,
                "package_quantity": 1,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0092_restage_mando_ex54661a7100_evocar_photo")]
    operations = [
        migrations.RunPython(stage_exact_ssangyong_photo, migrations.RunPython.noop),
    ]
