"""Apply the confirmed Kaspi repricing for AG Parts article 1109101XGW01A."""
from django.db import migrations


def apply_price(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    Product.objects.using(schema_editor.connection.alias).filter(
        pk=2112,
        article="1109101XGW01A",
        seller_name="AG Parts",
        status="active",
        price=1150,
    ).update(price=1100)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0066_reprice_agparts_151000151aa_20261010")]

    operations = [
        migrations.RunPython(apply_price, migrations.RunPython.noop)
    ]
