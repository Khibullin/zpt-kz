"""Align five AG Parts website prices with the Kaspi upload on 2026-09-27."""

from decimal import Decimal

from django.db import migrations


PRICES = (
    (1983, '151000025AA', '1050', '955'),
    (2135, '2032047000', '2770', '2700'),
    (2123, '8114010U8520', '2189', '1920'),
    (1988, 'T151109111', '1700', '1660'),
    (2129, 'X0390000206', '3034', '2960'),
)


def set_prices(apps, schema_editor):
    Product = apps.get_model('catalog', 'Product')
    alias = schema_editor.connection.alias

    # Check every row before changing any price. The migration transaction
    # rolls back all five updates if a concurrent edit or mismatch is found.
    updates = []
    for product_id, article, old, new in PRICES:
        qs = Product.objects.using(alias).select_for_update().filter(
            id=product_id, article=article
        )
        product = qs.first()
        if product is None:
            # A fresh installation can have an empty catalogue.
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
