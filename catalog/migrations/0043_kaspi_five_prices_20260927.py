"""Align confirmed AG Parts website prices with Kaspi on 2026-09-27."""

from decimal import Decimal

from django.db import migrations


PRICES = (
    (1983, '151000025AA', '1050', '955'),
    (1988, 'T151109111', '1700', '1660'),
)


def set_prices(apps, schema_editor):
    Product = apps.get_model('catalog', 'Product')
    alias = schema_editor.connection.alias
    updates = []
    for product_id, article, old, new in PRICES:
        product = Product.objects.using(alias).select_for_update().filter(
            id=product_id, article=article
        ).first()
        if product is None:
            if not Product.objects.using(alias).exists():
                return
            raise RuntimeError(f'Missing exact product {product_id} / {article}')
        if product.price == Decimal(new):
            continue
        if product.price != Decimal(old):
            raise RuntimeError(f'Unexpected website price for {article}: {product.price}')
        updates.append((product_id, article, Decimal(new)))

    for product_id, article, new_price in updates:
        Product.objects.using(alias).filter(id=product_id, article=article).update(
            price=new_price
        )


class Migration(migrations.Migration):
    dependencies = [('catalog', '0042_geely_2032047000_retail_price')]

    operations = [migrations.RunPython(set_prices, migrations.RunPython.noop)]
