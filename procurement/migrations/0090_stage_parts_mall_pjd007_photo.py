from django.db import migrations


SOURCE_URL = "https://catalog.parts-mall.com/shop-detail?factory_no=PJD-007"
IMAGE_URL = "https://catalog.parts-mall.com/images/PJD-007_0300-1F.jpg"


def stage_exact_parts_mall_photo(apps, schema_editor):
    """Stage one owner-reviewed exact photo; never change trade or Kaspi data."""
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
            pk=25285,
            supplier_id=5,
            brand="Parts-Mall",
            article="PJD007",
            stock="2",
            cost="8790.00",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=7013,
            article="PJD007",
            status="active",
            main_image="",
            supplier_thumbnail="",
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4986,
            product_id=7013,
            offer_id=25285,
            status="needs_source",
            source_url="",
        ).first()
        if offer is not None and product is not None and task is not None:
            task.source_url = SOURCE_URL
            task.status = "web_ready"
            task.note = (
                "Адресная публикация подтверждённого фото точного Parts-Mall PJD-007 "
                "из официального каталога производителя."
            )
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": "weekend-20261114-o",
                "source_kind": "manufacturer_current_catalogue",
                "source_url": SOURCE_URL,
                "image_url": IMAGE_URL,
                "brand": "Parts-Mall",
                "article": "PJD007",
                "source_article": "PJD-007",
                "execution": "front gas shock absorber",
                "oem_numbers": [
                    "P4430106200",
                    "P4430106202",
                    "P4430106220",
                ],
                "package_quantity": 1,
                "checked_on": "2026-11-14",
                "exact_official_page": True,
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            staged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_pjd007_photo_staged",
            details={
                "batch": "weekend-20261114-o",
                "offer_id": 25285,
                "product_id": 7013,
                "task_id": 4986,
                "photo_tasks": staged,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0089_retry_reviewed_weekend_20261114_n_live")]
    operations = [
        migrations.RunPython(stage_exact_parts_mall_photo, migrations.RunPython.noop),
    ]
