"""Sync AG Parts 2032047000 with the current Kaspi seller offer."""
from django.db import migrations


def apply_price(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    Product.objects.using(schema_editor.connection.alias).filter(
        pk=2135,
        article="2032047000",
        seller_name="AG Parts",
        status="active",
        price=2547,
    ).update(price=2466)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0069_sync_151000025aa_kaspi_price_20261010")]

    operations = [
        migrations.RunPython(apply_price, migrations.RunPython.noop)
    ]
