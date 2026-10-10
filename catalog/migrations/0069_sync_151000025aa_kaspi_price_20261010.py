"""Sync AG Parts 151000025AA with the current Kaspi seller offer."""
from django.db import migrations


def apply_price(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    Product.objects.using(schema_editor.connection.alias).filter(
        pk=1983,
        article="151000025AA",
        seller_name="AG Parts",
        status="active",
        price=847,
    ).update(price=766)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0068_sync_x0390000206_kaspi_promo_price_20261010")]

    operations = [
        migrations.RunPython(apply_price, migrations.RunPython.noop)
    ]
