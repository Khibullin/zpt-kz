from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("repricer", "0002_kaspicompetitoringestbatch"),
    ]

    operations = [
        migrations.AddField(
            model_name="kaspicompetitoroffersnapshot",
            name="city_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=12, verbose_name="Город Kaspi (код)"),
        ),
        migrations.AddField(
            model_name="kaspicompetitoringestbatch",
            name="city_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=12, verbose_name="Город Kaspi (код)"),
        ),
    ]
