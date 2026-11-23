from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("repricer", "0008_apify_output_prices")]
    operations = [
        migrations.AddField(
            model_name="kaspiactiveexportrow",
            name="competitor_scan_note",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
