from django.db import migrations


SOURCE_URL = "https://www.chinesecar.parts/products/changan-c281f260203-1100-2090.html"
CONFIRMATION_URL = "https://kaspi.kz/shop/p/jashi-zadnii-c281f260203-1100-1-sht-130050271/"


def add_exact_jashi_fitment(apps, schema_editor):
    """Add only the exact confirmed make/model fitment; keep unknown years and engines empty."""
    if schema_editor.connection.alias != "default":
        return

    from django.db import transaction

    Product = apps.get_model("catalog", "Product")
    Task = apps.get_model("catalog", "SupplierPhotoTask")
    Offer = apps.get_model("procurement", "Offer")
    VehicleFitment = apps.get_model("procurement", "VehicleFitment")
    Activity = apps.get_model("procurement", "Activity")

    fitment_added = 0
    with transaction.atomic():
        offer = Offer.objects.select_for_update().filter(
            pk=25088,
            supplier_id=5,
            brand="JASHI",
            article="C281F260203-1100",
            stock="6",
            cost="21400.00",
            kaspi_price="31390.00",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=6788,
            article="C281F260203-1100",
            status="active",
            main_image="",
            supplier_thumbnail="",
            price=27820,
            cost_price=21400,
            stock_qty=6,
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4761,
            product_id=6788,
            offer_id=25088,
            status="needs_source",
            source_url="",
        ).first()
        fitment_empty = not VehicleFitment.objects.filter(offer_id=25088).exists()

        if offer is not None and product is not None and task is not None and fitment_empty:
            VehicleFitment.objects.create(
                offer_id=25088,
                make="CHANGAN",
                model="UNI-V",
                details=(
                    "Амортизатор задней подвески; OEM C281F260203-1100; 1 шт. "
                    "Годы, поколение и двигатель источниками не подтверждены."
                ),
                source_url=SOURCE_URL,
                verified=True,
                configurations=[{
                    "placement": "rear",
                    "part_type": "rear shock absorber assembly",
                    "oem_article": "C281F260203-1100",
                    "package_quantity": 1,
                    "years": None,
                    "engine": None,
                    "source_url": SOURCE_URL,
                    "confirmation_url": CONFIRMATION_URL,
                    "restriction": "Confirm VIN and model year before ordering; source does not publish a year range.",
                }],
            )
            fitment_added = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_jashi_c281f260203_1100_fitment",
            details={
                "batch": "weekend-20261115-j",
                "offer_id": 25088,
                "product_id": 6788,
                "task_id": 4761,
                "fitment_rows_added": fitment_added,
                "photo_tasks_staged": 0,
                "photo_reason": "exact source image returns HTTP 403; no bypass attempted",
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0099_stage_haval_2804201xkn01a_photo_fitment")]
    operations = [
        migrations.RunPython(add_exact_jashi_fitment, migrations.RunPython.noop),
    ]
