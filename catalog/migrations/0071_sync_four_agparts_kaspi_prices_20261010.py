"""Sync four active AG Parts products to freshly observed Kaspi offer prices."""
from django.db import migrations


PRICE_UPDATES = (
    (2113, "1109104XGW02A", 1400, 1065),
    (2115, "151000079AA", 729, 680),
    (2118, "6600131687", 2395, 2316),
    (2048, "8100422XNZ01A", 3740, 2920),
)


def apply_prices(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    products = Product.objects.using(schema_editor.connection.alias)
    for product_id, article, expected_price, new_price in PRICE_UPDATES:
        products.filter(
            pk=product_id,
            article=article,
            seller_name="AG Parts",
            status="active",
            price=expected_price,
        ).update(price=new_price)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0070_sync_2032047000_kaspi_price_20261010")]

    operations = [
        migrations.RunPython(apply_prices, migrations.RunPython.noop)
    ]
