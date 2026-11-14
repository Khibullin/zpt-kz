from django.db import migrations


def restage_after_parser_live(apps, schema_editor):
    if schema_editor.connection.alias != "default":
        return

    from django.db import transaction

    Task = apps.get_model("catalog", "SupplierPhotoTask")
    Product = apps.get_model("catalog", "Product")
    Activity = apps.get_model("procurement", "Activity")

    with transaction.atomic():
        product = Product.objects.select_for_update().filter(
            pk=6703,
            article="2905110AKZ16A",
            status="active",
            main_image="",
        ).first()
        changed = 0
        if product is not None:
            task = Task.objects.select_for_update().filter(
                pk=4676,
                product_id=6703,
                offer_id=25177,
                status="skipped",
                source_url="https://rebornfj.com/product-detail/front-left-shock-absorber-assembly-for_197499.html",
                evidence__reviewed_batch="weekend-20261114-n",
            ).first()
            if task is not None:
                task.status = "weekend14n_ready"
                task.note = "Адресный повтор после подтверждённого запуска исправленного строгого разбора источника."
                task.save(update_fields=["status", "note", "updated_at"])
                changed = 1
        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_haval_photo_retry_after_parser_live",
            details={"batch": "weekend-20261114-n", "photo_tasks": changed},
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0088_retry_reviewed_weekend_20261114_n")]
    operations = [migrations.RunPython(restage_after_parser_live, migrations.RunPython.noop)]
