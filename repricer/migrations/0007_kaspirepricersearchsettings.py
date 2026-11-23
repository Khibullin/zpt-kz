from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("repricer", "0006_kaspiactiveexportrow"),
    ]
    operations = [
        migrations.CreateModel(
            name="KaspiRepricerSearchSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cities", models.JSONField(default=list, verbose_name="Города Kaspi")),
                ("cards_per_product", models.PositiveSmallIntegerField(default=2, verbose_name="Карточек на товар")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Настройки поиска цен Kaspi",
                "verbose_name_plural": "Настройки поиска цен Kaspi",
            },
        ),
    ]
