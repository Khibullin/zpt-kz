from django.db import migrations


PHOTO_SOURCE_URL = "https://kaspi.kz/shop/p/hyundai-kia-balka-zadnei-podveski-55100h5600-146288405/"
IMAGE_URL = "https://resources.cdn-kaspi.kz/img/m/p/p58/pa8/66804531.png?format=gallery-medium"
FITMENT_G4LC_URL = "https://partsouq.com/de/catalog/genuine/parts?c=Hyundai&gid=504&q=Z94K241BBMR254248&ssd=%24%2AKwG6h7qr3rvt-_DJ9a6y_fmq28va2tnGuePv4aSr662yq8bVhJGHycC8v7q9vrjz8-Hlq7StwLzduJrr9bnPrKOq-qy285nhpKv4rbKr1LXllPG8uMzNxdu8ueXt97Cr8_HXydznkpiN4v_WtsLYr_KOgIO-sby-9tb-_6Wflc3Bx8zEzMfIlJGX293cxsbO8NOhrLXI07e7w7u6vZOdjtq7u7u6vbYAAAAAv2rcYA%3D%3D%24&vid=791418026"
FITMENT_G4FG_URL = "https://ftp.gforceparts.com/en/catalog/genuine/parts?c=Hyundai&gid=478&q=Z94K241CBMR225197&ssd=%24%2AKwGmi6qz4PDgxfX54cb_g4TJ69zy4sOg87m7n--38bGut9fe7-_957eosdq37-6bprfCp_Hn7NXpsq7h5bbH18bGxdql__P9uLf3sa632smYjZvV3KCjpqGipO_v_fm3qLHcoMGkifHppdOwv7bmsKrvhf24t-SxrrfIqfmI7aCk0dHZx6Ci-PLmo7fv7cvV2fuas5Xm5_XZ3_qz7pKcn6KtoKLqyuLjuYOJ0d3b0NjQ29SIjYvHwcDa2tLsz72wqdTPq6ffp6ahjoGSxqegpqWspQAAAACmydqV%24&vid=1565532914"


def stage_exact_hyundai_kia_beam(apps, schema_editor):
    """Stage one exact 55100H5600 photo and VIN-catalogue fitment only."""
    if schema_editor.connection.alias != "default":
        return

    from django.db import transaction

    Task = apps.get_model("catalog", "SupplierPhotoTask")
    Product = apps.get_model("catalog", "Product")
    Offer = apps.get_model("procurement", "Offer")
    VehicleFitment = apps.get_model("procurement", "VehicleFitment")
    Activity = apps.get_model("procurement", "Activity")

    staged = 0
    fitment_added = 0
    with transaction.atomic():
        offer = Offer.objects.select_for_update().filter(
            pk=25352,
            supplier_id=5,
            brand="HYUNDAI-KIA",
            article="55100H5600",
            stock="1",
            cost="87300.00",
            kaspi_price="105600.00",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=6915,
            article="55100H5600",
            status="active",
            main_image="",
            supplier_thumbnail="",
            price=113490,
            cost_price=87300,
            stock_qty=1,
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4888,
            product_id=6915,
            offer_id=25352,
            status="needs_source",
            source_url="",
        ).first()
        fitment_empty = not VehicleFitment.objects.filter(offer_id=25352).exists()

        if offer is not None and product is not None and task is not None and fitment_empty:
            configurations = [
                {
                    "generation": None,
                    "years": "2021",
                    "year": 2021,
                    "engine": "G4LC",
                    "market": "EUR;GEN",
                    "vehicle_date": "29.11.2020",
                    "execution": "torsion axle complete",
                    "placement": "rear suspension",
                    "package_quantity": 1,
                    "source_url": FITMENT_G4LC_URL,
                },
            ]
            VehicleFitment.objects.create(
                offer_id=25352,
                make="HYUNDAI",
                model="Accent / Solaris",
                details=(
                    "Модельный год 2021; задняя торсионная балка в сборе. "
                    "VIN-каталог подтверждает двигатель G4LC; "
                    "поколение и иные годы источниками не подтверждены."
                ),
                source_url=FITMENT_G4LC_URL,
                verified=True,
                configurations=configurations,
            )
            fitment_added = 1

            task.source_url = PHOTO_SOURCE_URL
            task.status = "weekend15o_ready"
            task.note = (
                "Адресная публикация предметного фото точного HYUNDAI-KIA 55100H5600; "
                "применимость ограничена точным VIN-каталогом 2021 года."
            )
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": "weekend-20261115-o",
                "source_kind": "exact_kaspi_card_and_vin_catalogues",
                "source_url": PHOTO_SOURCE_URL,
                "fitment_source_urls": [FITMENT_G4LC_URL],
                "temporarily_unavailable_sources": [{
                    "url": FITMENT_G4FG_URL,
                    "reason": "HTTP 502 при повторном открытии 2026-11-15; не использован для сохранённой применимости",
                }],
                "image_url": IMAGE_URL,
                "verified_image_size": "750x241",
                "verified_image_sha256": "2504af577c30a4a0cb5670e642766acfe2566670e219ad6515c37e98c0fe710b",
                "brand": "HYUNDAI-KIA",
                "source_brand": "HYUNDAI / KIA",
                "article": "55100H5600",
                "execution": "torsion axle complete",
                "placement": "rear suspension",
                "package_quantity": 1,
                "fitment_models": ["HYUNDAI Accent / Solaris"],
                "fitment_years": [2021],
                "fitment_engines": ["G4LC"],
                "generation_confirmed": False,
                "checked_on": "2026-11-15",
                "exact_card": True,
                "visual_check": (
                    "real isolated black rear torsion beam assembly; no car, "
                    "diagram, analogue or placeholder"
                ),
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            staged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_hyundai_kia_55100h5600_photo_fitment_staged",
            details={
                "batch": "weekend-20261115-o",
                "offer_id": 25352,
                "product_id": 6915,
                "task_id": 4888,
                "photo_tasks": staged,
                "fitment_rows_added": fitment_added,
                "trade_data_changed": False,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0109_fix_jashi_8405120p3011_set_quantity")]
    operations = [
        migrations.RunPython(stage_exact_hyundai_kia_beam, migrations.RunPython.noop),
    ]
