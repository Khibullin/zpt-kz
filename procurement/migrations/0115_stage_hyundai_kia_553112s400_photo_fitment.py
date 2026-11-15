from django.db import migrations


PHOTO_SOURCE_URL = (
    "https://kaspi.kz/shop/p/hyundai-kia-zadnii-553112s400-1-sht-142570155/"
    "?c=750000000"
)
IMAGE_URL = (
    "https://resources.cdn-kaspi.kz/img/m/p/p9b/p96/53188476.png"
    "?format=gallery-medium"
)
HYUNDAI_FITMENT_URL = (
    "https://partsouq.com/en/catalog/genuine/diagram?c=HYUNDAI202404&number=553112S400"
    "&ssd=%24%2AKwHJ9MnYrcieiIO6ht3BjorZqbipqaqpsvidgNnW35nZwN-3-_n-v7u0zsvIyc-W"
    "jpzZid_G2bvfgtzz8Kylq6uUsbSLkpX52oSio7vMxc2T0u-LiYm8tL-wgf3cw426y"
    "MnIy8zN8ZiAywAAAABcGbua%24&vid=1092930139"
)
HYUNDAI_COMPATIBILITY_URL = (
    "https://partsouq.com/shop/product/553112S400-hyundai-kia-shock-absorber-assy-rr/14115351"
)
KIA_FITMENT_URL = (
    "https://www.autoopt.ru/catalog/430106-"
    "amortizator_hyundai_ix35_10__4wd_kia_sportage_10__4wd_zadnij_"
    "levyjpravyj_gazovyj_kyb/pdf"
)


def stage_exact_hyundai_kia_553112s400(apps, schema_editor):
    """Stage an exact clean product image and bounded OEM/cross-catalog fitment."""
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
            pk=25131,
            supplier_id=5,
            brand="HYUNDAI-KIA",
            article="553112S400",
            stock="4",
            cost="11650.00",
            kaspi_price="18560.00",
            photo_url="",
        ).first()
        product = Product.objects.select_for_update().filter(
            pk=6714,
            article="553112S400",
            status="active",
            main_image="",
            supplier_thumbnail="",
            price=15145,
            cost_price=11650,
            stock_qty=4,
        ).first()
        task = Task.objects.select_for_update().filter(
            pk=4687,
            product_id=6714,
            offer_id=25131,
            status="needs_source",
            source_url="",
        ).first()
        fitment_empty = not VehicleFitment.objects.filter(offer_id=25131).exists()

        if offer is not None and product is not None and task is not None and fitment_empty:
            VehicleFitment.objects.create(
                offer_id=25131,
                make="HYUNDAI",
                model="ix35 / Tucson 10",
                details=(
                    "Оригинальный каталог HYUNDAI подтверждает задний амортизатор "
                    "553112S400 для ix35/Tucson 10, европейская сборка Slovakia, "
                    "2010–2011: дизель 2.0 R, АКПП 6 ступеней, 4WD, спецификация "
                    "подвески Russia. Другие региональные периоды из сводной карточки "
                    "не объединены в один диапазон."
                ),
                source_url=HYUNDAI_FITMENT_URL,
                verified=True,
                configurations=[
                    {
                        "generation": "IX35/TUCSON 10 (SLOVAKIA PLANT-EUR)",
                        "years": "2010–2011",
                        "year_from": 2010,
                        "year_to": 2011,
                        "engine": "2.0 R diesel",
                        "transmission": "6-speed automatic",
                        "drivetrain": "4WD",
                        "suspension_spec": "Russia",
                        "placement": "rear axle",
                        "side": "left/right",
                    },
                ],
            )
            VehicleFitment.objects.create(
                offer_id=25131,
                make="KIA",
                model="Sportage III",
                details=(
                    "Проверенный каталог по кросс-номеру 55311-2S400 подтверждает "
                    "задний газовый амортизатор слева/справа для Sportage III 4WD "
                    "от 2010 года. Конечный год и двигатели источником не подтверждены."
                ),
                source_url=KIA_FITMENT_URL,
                verified=True,
                configurations=[
                    {
                        "generation": "Sportage III",
                        "years": "2010–",
                        "year_from": 2010,
                        "year_to": None,
                        "engine": None,
                        "drivetrain": "4WD",
                        "placement": "rear axle",
                        "side": "left/right",
                        "execution": "gas shock absorber",
                    },
                ],
            )
            fitment_added = 2

            task.source_url = PHOTO_SOURCE_URL
            task.status = "weekend15t_ready"
            task.note = (
                "Адресная публикация чистого предметного фото точного HYUNDAI-KIA "
                "553112S400; применимость ограничена подтверждёнными OEM и кросс-каталогами."
            )
            task.evidence = {
                **(task.evidence or {}),
                "reviewed_batch": "weekend-20261115-t",
                "source_kind": "exact_kaspi_card_oem_and_cross_catalogues",
                "source_url": PHOTO_SOURCE_URL,
                "fitment_source_urls": [
                    HYUNDAI_FITMENT_URL,
                    HYUNDAI_COMPATIBILITY_URL,
                    KIA_FITMENT_URL,
                ],
                "image_url": IMAGE_URL,
                "verified_image_size": "516x353",
                "brand": "HYUNDAI-KIA",
                "source_brand": "HYUNDAI / KIA",
                "article": "553112S400",
                "execution": "rear shock absorber",
                "placement": "rear axle, left/right",
                "package_quantity": 1,
                "fitment_models": [
                    "HYUNDAI ix35 / Tucson 10",
                    "KIA Sportage III",
                ],
                "fitment_years": ["2010–2011", "2010–"],
                "fitment_engines": ["2.0 R diesel"],
                "fitment_restrictions": [
                    "HYUNDAI: 6-speed automatic, 4WD, Russia suspension spec",
                    "KIA: 4WD; end year and engines not confirmed",
                ],
                "checked_on": "2026-11-15",
                "exact_card": True,
                "visual_check": (
                    "clean isolated rear shock absorber; no car, diagram, analogue, "
                    "placeholder or promotional text"
                ),
            }
            task.save(update_fields=["source_url", "status", "note", "evidence", "updated_at"])
            staged = 1

        Activity.objects.create(
            user_id=None,
            action="reviewed_weekend_hyundai_kia_553112s400_photo_fitment_staged",
            details={
                "batch": "weekend-20261115-t",
                "offer_id": 25131,
                "product_id": 6714,
                "task_id": 4687,
                "photo_tasks": staged,
                "fitment_rows_added": fitment_added,
                "trade_data_changed": False,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0114_stage_ssangyong_7145034004_photo_fitment")]
    operations = [
        migrations.RunPython(stage_exact_hyundai_kia_553112s400, migrations.RunPython.noop),
    ]
