"""Align four active AG Parts website prices to observed Kaspi offers."""

from decimal import Decimal

from django.db import migrations


PRICES = (
    (2114, '1109130U2400', '3800', '2180'),
    (2115, '151000079AA', '1000', '810'),
    (2137, 'J691109111', '1980', '1710'),
    (1995, 'S111F2801031700', '2200', '1624'),
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
    dependencies = [('catalog', '0044_kaspi_three_prices_20260927')]

    operations = [migrations.RunPython(set_prices, migrations.RunPython.noop)]
