from django.db import migrations


SOURCE_URL = "https://partsouq.com/shop/product/553102P500-hyundai-kia-shock-absorber-assy/17998151"
IMAGE_URL = "https://partsouq.com/assets/tesseract/assets/partsimages/Hyundai-/-KIA/553102P500.jpg"


def stage_exact_hyundai_kia_photo(apps, schema_editor):
    """Stage one exact reviewed PartSouq photo; preserve trade and fitment data."""
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
            pk=25129, supplier_id=5, brand="HYUNDAI-KIA",
            article="553102P500", stock="6", cost="33700.00",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=6713, article="553102P500", status="active",
            main_image="", supplier_thumbnail="", price=43810,
            cost_price=33700, stock_qty=6,
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4686, product_id=6713, offer_id=25129,
            status="needs_source", source_url="",
        ).first()
        fitments = VehicleFitment.objects.filter(offer_id=25129, verified=True)
        fitment_ok = (
            fitments.count() == 1
            and fitments.filter(
                make="KIA", model="New Sorento R (XM)",
                year_from="2012-07", year_to="2014-08",
            ).exists()
        )
        if offer is not None and product is not None and task is not None and fitment_ok:
            task.source_url = SOURCE_URL
            task.status = "weekend15g_ready"
            task.note = (
                "Адресная публикация предметного фото точной карточки PartSouq "
                "HYUNDAI-KIA 553102P500, задний амортизатор в сборе, 1 шт."
            )
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": "weekend-20261115-g",
                "source_kind": "exact_international_catalogue_card",
                "source_url": SOURCE_URL,
                "image_url": IMAGE_URL,
                "verified_image_sha256": "a1582312e376f6d01b42ca7ec57478cf614dc1a891ce4e556c325f97cad2a6b1",
                "verified_image_size": "1920x1280",
                "brand": "HYUNDAI-KIA",
                "article": "553102P500",
                "source_article": "553102P500",
                "execution": "rear shock absorber assembly",
                "placement": "rear",
                "package_quantity": 1,
                "checked_on": "2026-11-15",
                "exact_card": True,
                "visual_check": "real rear shock absorber; no car, diagram, analogue or placeholder",
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            staged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_hyundai_kia_553102p500_photo_staged",
            details={
                "batch": "weekend-20261115-g",
                "offer_id": 25129,
                "product_id": 6713,
                "task_id": 4686,
                "photo_tasks": staged,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0096_stage_trialli_ag03007_globalparts_photo")]
    operations = [
        migrations.RunPython(stage_exact_hyundai_kia_photo, migrations.RunPython.noop),
    ]
