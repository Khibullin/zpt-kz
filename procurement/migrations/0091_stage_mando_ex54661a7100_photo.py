from django.db import migrations


SOURCE_URL = "https://www.autodoc.co.uk/mando/14579627"
IMAGE_URL = "https://cdn.autodoc.de/thumb?id=14579627&m=0&n=0&lng=en&rev=94078121"


def stage_exact_mando_photo(apps, schema_editor):
    """Stage one exact reviewed Mando photo; never change trade or Kaspi data."""
    if schema_editor.connection.alias != "default":
        return

    from django.db import transaction

    Task = apps.get_model("catalog", "SupplierPhotoTask")
    Product = apps.get_model("catalog", "Product")
    Offer = apps.get_model("procurement", "Offer")
    Activity = apps.get_model("procurement", "Activity")

    staged = 0
    with transaction.atomic():
        offer = Offer.objects.select_for_update().filter(
            pk=25268,
            supplier_id=5,
            brand="Mando",
            article="EX54661A7100",
            stock="1",
            cost="20400.00",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=6994,
            article="EX54661A7100",
            status="active",
            main_image="",
            supplier_thumbnail="",
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4967,
            product_id=6994,
            offer_id=25268,
            status="needs_source",
            source_url="",
        ).first()
        if offer is not None and product is not None and task is not None:
            task.source_url = SOURCE_URL
            # Old live consumers do not activate this status. The new release
            # activates it only after the strict AUTODOC parser is deployed.
            task.status = "weekend15a_ready"
            task.note = (
                "Адресная публикация фото точного Mando EX54661A7100 из "
                "проверенного международного каталога; исполнение и 1 шт. подтверждены."
            )
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": "weekend-20261115-a",
                "source_kind": "checked_international_exact_catalogue",
                "source_url": SOURCE_URL,
                "image_url": IMAGE_URL,
                "brand": "Mando",
                "article": "EX54661A7100",
                "execution": "front right gas-pressure twin-tube suspension strut",
                "paired_article": "EX54651A7100",
                "oem_number": "54661A7100",
                "ean": "8809746745136",
                "package_quantity": 1,
                "checked_on": "2026-11-15",
                "exact_catalogue_page": True,
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            staged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_mando_ex54661a7100_photo_staged",
            details={
                "batch": "weekend-20261115-a",
                "offer_id": 25268,
                "product_id": 6994,
                "task_id": 4967,
                "photo_tasks": staged,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0090_stage_parts_mall_pjd007_photo")]
    operations = [
        migrations.RunPython(stage_exact_mando_photo, migrations.RunPython.noop),
    ]
