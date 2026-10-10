"""Sync AG Parts X0390000206 price with its live public Kaspi offer."""
from django.db import migrations


def apply_price(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    Product.objects.using(schema_editor.connection.alias).filter(
        pk=2129,
        article="X0390000206",
        seller_name="AG Parts",
        status="active",
        price=2960,
    ).update(price=1169)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0067_reprice_agparts_1109101xgw01a_20261010")]

    operations = [
        migrations.RunPython(apply_price, migrations.RunPython.noop)
    ]
