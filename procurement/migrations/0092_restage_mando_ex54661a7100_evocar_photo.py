from django.db import migrations


SOURCE_URL = "https://evocar.ua/ru/mando/ex54661a7100"
IMAGE_URL = "https://cdn.evocar.ua/786ef0b604d0b4428b926fc1db1a4185/mando-ex54661a7100.jpg"
AUTODOC_URL = "https://www.autodoc.co.uk/mando/14579627"


def restage_exact_mando_photo(apps, schema_editor):
    """Move one blocked task to a newly verified source without bypassing the first source."""
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
            status="blocked",
            source_url=AUTODOC_URL,
        ).first()
        if offer is not None and product is not None and task is not None:
            old_evidence = task.evidence or {}
            task.source_url = SOURCE_URL
            # The currently live process does not activate this state. The new
            # release activates it only after the strict EVOCAR parser is live.
            task.status = "weekend15b_ready"
            task.note = (
                "Первый точный источник вернул HTTP 403 и не повторялся. "
                "Подготовлен другой подтверждённый каталог точного Mando EX54661A7100."
            )
            task.evidence = {
                **old_evidence,
                "previous_source_attempts": [
                    {
                        "url": AUTODOC_URL,
                        "result": "temporary_source_unavailable_http_403",
                        "checked_on": "2026-11-15",
                        "retry_policy": "do_not_retry_without_new_source_state",
                    }
                ],
                "block_kind": None,
                "reviewed_batch": "weekend-20261115-b",
                "source_kind": "checked_international_exact_catalogue",
                "source_url": SOURCE_URL,
                "image_url": IMAGE_URL,
                "brand": "Mando",
                "article": "EX54661A7100",
                "execution": "front right gas-oil twin-tube suspension strut",
                "paired_article": "EX54651A7100",
                "oem_number": "54661A7100",
                "package_quantity": 1,
                "checked_on": "2026-11-15",
                "exact_catalogue_page": True,
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            staged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_mando_ex54661a7100_photo_restaged",
            details={
                "batch": "weekend-20261115-b",
                "offer_id": 25268,
                "product_id": 6994,
                "task_id": 4967,
                "photo_tasks": staged,
                "previous_source_result": "temporary_source_unavailable_http_403",
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0091_stage_mando_ex54661a7100_photo")]
    operations = [
        migrations.RunPython(restage_exact_mando_photo, migrations.RunPython.noop),
    ]
