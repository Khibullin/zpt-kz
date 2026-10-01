"""Align zpt.kz with Kaspi prices observed on 2026-10-01."""

from django.db import migrations


PRICES = (
    (1983, '151000025AA', 955, 930),
    (2135, '2032047000', 2700, 2630),
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
        if product.price == new:
            continue
        if product.price != old:
            raise RuntimeError(
                f'Unexpected website price for {article}: {product.price}'
            )
        updates.append((product_id, article, new))

    for product_id, article, new_price in updates:
        Product.objects.using(alias).filter(
            id=product_id, article=article
        ).update(price=new_price)


class Migration(migrations.Migration):
    dependencies = [('catalog', '0053_great_wall_poer_maintenance_kit')]

    operations = [migrations.RunPython(set_prices, migrations.RunPython.noop)]
