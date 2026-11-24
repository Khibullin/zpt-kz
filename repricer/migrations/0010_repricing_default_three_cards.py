from django.db import migrations, models


def apply_approved_cards(apps, schema_editor):
    Settings = apps.get_model("repricer", "KaspiRepricerSearchSettings")
    # Approved three candidates; retain the existing selected cities.
    Settings.objects.filter(cards_per_product=2).update(cards_per_product=3)


class Migration(migrations.Migration):
    dependencies = [("repricer", "0009_kaspiactive_scan_note")]
    operations = [
        migrations.AlterField(
            model_name="kaspirepricersearchsettings",
            name="cards_per_product",
            field=models.PositiveSmallIntegerField(
                default=3, verbose_name="Карточек на запрос",
            ),
        ),
        migrations.RunPython(apply_approved_cards, migrations.RunPython.noop),
    ]
