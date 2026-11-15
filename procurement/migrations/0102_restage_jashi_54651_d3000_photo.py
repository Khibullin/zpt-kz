from django.db import migrations


SOURCE_URL = "https://shop.globalparts.kz/catalog/card/210534"


def restage_exact_jashi_photo(apps, schema_editor):
    """Retry the one exact task after deploying its immutable-image guard."""
    if schema_editor.connection.alias != "default":
        return

    from django.db import transaction

    Task = apps.get_model("catalog", "SupplierPhotoTask")
    Product = apps.get_model("catalog", "Product")
    Offer = apps.get_model("procurement", "Offer")
    Fitment = apps.get_model("procurement", "VehicleFitment")
    Activity = apps.get_model("procurement", "Activity")

    restaged = 0
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
            status="skipped",
            source_url=SOURCE_URL,
            evidence__reviewed_batch="weekend-20261115-j",
        ).first()
        fitments_ok = Fitment.objects.filter(
            offer_id=25213,
            verified=True,
            source_url=SOURCE_URL,
        ).count() == 2

        if offer is not None and product is not None and task is not None and fitments_ok:
            task.status = "weekend15j_ready"
            task.note = (
                "Повтор одной адресной задачи после добавления exact-card guard; "
                "источник, изображение и live identity повторно проверены."
            )
            task.save(update_fields=["status", "note", "updated_at"])
            restaged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_jashi_54651_d3000_photo_restaged",
            details={
                "batch": "weekend-20261115-j",
                "offer_id": 25213,
                "product_id": 6764,
                "task_id": 4737,
                "photo_tasks": restaged,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0101_stage_jashi_54651_d3000_photo_fitment")]
    operations = [
        migrations.RunPython(restage_exact_jashi_photo, migrations.RunPython.noop),
    ]
