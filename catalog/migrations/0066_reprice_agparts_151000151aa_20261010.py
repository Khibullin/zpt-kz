"""Apply the confirmed Kaspi repricing for AG Parts article 151000151AA."""
from django.db import migrations


def apply_price(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    Product.objects.using(schema_editor.connection.alias).filter(
        pk=2134,
        article="151000151AA",
        seller_name="AG Parts",
        status="active",
        price=2950,
    ).update(price=2819)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0065_apply_confirmed_agparts_reprices_20261010")]

    operations = [
        migrations.RunPython(apply_price, migrations.RunPython.noop)
    ]
