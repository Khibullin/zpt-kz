"""Apply the confirmed Kaspi reprices to the AG Parts website catalog.

Only the five exact active AG Parts rows and their observed old prices are eligible.
This migration deliberately leaves stock, applicability, listings, and other sellers alone.
"""
from django.db import migrations


PRICE_UPDATES = (
    (1983, "151000025AA", 928, 847),
    (2115, "151000079AA", 810, 729),
    (2135, "2032047000", 2628, 2547),
    (2123, "8114010U8520", 1919, 1838),
    (1988, "T151109111", 1600, 1519),
)


def apply_confirmed_prices(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    database = schema_editor.connection.alias

    for product_id, article, old_price, new_price in PRICE_UPDATES:
        Product.objects.using(database).filter(
            pk=product_id,
            article=article,
            seller_name="AG Parts",
            status="active",
            price=old_price,
        ).update(price=new_price)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0064_pin_existing_kaspi_links_to_agparts")]

    operations = [
        migrations.RunPython(apply_confirmed_prices, migrations.RunPython.noop)
    ]
