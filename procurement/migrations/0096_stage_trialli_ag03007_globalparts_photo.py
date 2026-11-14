from django.db import migrations


SOURCE_URL = "https://shop.globalparts.kz/catalog/card/184634"
IMAGE_URL = "https://resources.cdn-kaspi.kz/img/m/p/h6d/h92/67650614853662.jpg?format=gallery-large"


def stage_exact_trialli_photo(apps, schema_editor):
    """Stage one exact reviewed KZ-catalogue photo; preserve trade and fitment data."""
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
            pk=25170, supplier_id=5, brand="TRIALLI",
            article="AG 03007", stock="4", cost="6300.00",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=6841, article="AG 03007", status="active",
            main_image="", supplier_thumbnail="", price=8190,
            cost_price=6300, stock_qty=4,
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4814, product_id=6841, offer_id=25170,
            status="needs_source", source_url="",
        ).first()
        fitments = VehicleFitment.objects.filter(offer_id=25170, verified=True)
        fitment_models = set(fitments.values_list("make", "model"))
        fitment_ok = fitments.count() == 5 and fitment_models == {
            ("UAZ", "3160 Closed Off-Road Vehicle"),
            ("UAZ", "PICKUP"),
            ("UAZ", "Patriot"),
            ("UAZ", "SIMBIR (3162) Closed Off-Road Vehicle"),
            ("UAZ", "UAZ 3160"),
        }
        if offer is not None and product is not None and task is not None and fitment_ok:
            task.source_url = SOURCE_URL
            task.status = "weekend15f_ready"
            task.note = (
                "Адресная публикация предметного фото точной карточки Global Parts KZ "
                "TRIALLI AG03007, передний левый/правый амортизатор, 1 шт."
            )
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": "weekend-20261115-f",
                "source_kind": "exact_kz_catalogue_card",
                "source_url": SOURCE_URL,
                "image_url": IMAGE_URL,
                "verified_image_sha256": "be528163f9a869771d76beabf475f162dad3c2c58a7d747a67e79c187d06da44",
                "verified_image_size": "627x373",
                "brand": "TRIALLI",
                "article": "AG 03007",
                "source_article": "AG03007",
                "execution": "front shock absorber",
                "placement": "front",
                "side": "left/right",
                "package_quantity": 1,
                "checked_on": "2026-11-15",
                "exact_card": True,
                "visual_check": "real shock absorber with TRIALLI package; no car or placeholder",
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            staged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_trialli_ag03007_photo_staged",
            details={
                "batch": "weekend-20261115-f",
                "offer_id": 25170,
                "product_id": 6841,
                "task_id": 4814,
                "photo_tasks": staged,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0095_stage_jashi_c281f260202_0200_kaspi_photo")]
    operations = [
        migrations.RunPython(stage_exact_trialli_photo, migrations.RunPython.noop),
    ]
