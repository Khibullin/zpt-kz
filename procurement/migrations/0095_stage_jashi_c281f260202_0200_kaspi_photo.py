from django.db import migrations


SOURCE_URL = "https://kaspi.kz/shop/p/jashi-perednii-c281f260202-0200-1-sht-130050225/"
IMAGE_URL = "https://resources.cdn-kaspi.kz/img/m/p/p62/p7d/8356815.png?format=gallery-large"


def stage_exact_jashi_photo(apps, schema_editor):
    """Stage one exact reviewed Kaspi photo; preserve all trade and fitment data."""
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
            pk=25082, supplier_id=5, brand="JASHI",
            article="C281F260202-0200", stock="1", cost="47200.00",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=6786, article="C281F260202-0200", status="active",
            main_image="", supplier_thumbnail="", price=61360,
            cost_price=47200, stock_qty=1,
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4759, product_id=6786, offer_id=25082,
            status="needs_source", source_url="",
        ).first()
        fitment_ok = VehicleFitment.objects.filter(
            offer_id=25082, make="CHANGAN", model="UNI-V", verified=True,
        ).exists()
        if offer is not None and product is not None and task is not None and fitment_ok:
            task.source_url = SOURCE_URL
            task.status = "weekend15e_ready"
            task.note = (
                "Адресная публикация предметного фото точной карточки Kaspi "
                "JASHI C281F260202-0200, комплектная передняя левая стойка, 1 шт."
            )
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": "weekend-20261115-e",
                "source_kind": "exact_kaspi_card",
                "source_url": SOURCE_URL,
                "image_url": IMAGE_URL,
                "verified_image_sha256_medium": "53dac93a8f2d4b24c513ab10165a205d24328e4f908cb018803642cd3daaf518",
                "verified_image_size_medium": "401x172",
                "brand": "JASHI",
                "article": "C281F260202-0200",
                "execution": "complete front-left strut with spring, dust boot and mount",
                "placement": "front",
                "side": "left",
                "package_quantity": 1,
                "checked_on": "2026-11-15",
                "exact_card": True,
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            staged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_jashi_c281_photo_staged",
            details={
                "batch": "weekend-20261115-e",
                "offer_id": 25082,
                "product_id": 6786,
                "task_id": 4759,
                "photo_tasks": staged,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0094_restage_ssangyong_4530132100_ebay_photo")]
    operations = [
        migrations.RunPython(stage_exact_jashi_photo, migrations.RunPython.noop),
    ]
