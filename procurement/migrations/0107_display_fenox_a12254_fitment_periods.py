from django.db import migrations


FITMENT_SOURCE_URL = "https://cartex.kz/part/a12254-fenox"
CONFIRMATION_URL = "https://auto-parts78.ru/catalog/fenox/a12254"


def display_exact_fenox_periods(apps, schema_editor):
    """Expose source-stated open periods to the public fitment table."""
    if schema_editor.connection.alias != "default":
        return

    VehicleFitment = apps.get_model("procurement", "VehicleFitment")
    Activity = apps.get_model("procurement", "Activity")

    fitment = VehicleFitment.objects.filter(
        pk=111531,
        offer_id=25142,
        make="DAEWOO",
        model="Matiz",
        source_url=FITMENT_SOURCE_URL,
        verified=True,
    ).first()
    updated = 0
    if fitment is not None:
        configs = fitment.configurations or []
        if (
            len(configs) == 2
            and configs[0].get("engine") == "F8CV 0.8"
            and configs[0].get("period") == "09.1998–present"
            and configs[1].get("engine") == "B10S 1.0"
            and configs[1].get("period") == "01.2003–present"
        ):
            configs[0]["years"] = "1998–н.в."
            configs[0]["date_from"] = "09.1998"
            configs[1]["years"] = "2003–н.в."
            configs[1]["date_from"] = "01.2003"
            for config in configs:
                config["confirmation_url"] = CONFIRMATION_URL
            fitment.configurations = configs
            fitment.save(update_fields=["configurations"])
            updated = 1

    Activity.objects.create(
        user_id=None,
        action="reviewed_weekend_fenox_a12254_fitment_periods_displayed",
        details={
            "batch": "weekend-20261115-m",
            "offer_id": 25142,
            "fitment_id": 111531,
            "rows_updated": updated,
            "periods": ["09.1998–present", "01.2003–present"],
        },
    )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0106_stage_fenox_a12254_photo_fitment")]
    operations = [
        migrations.RunPython(display_exact_fenox_periods, migrations.RunPython.noop),
    ]
