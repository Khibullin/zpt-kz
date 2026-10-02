"""Align zpt.kz with Kaspi prices observed on 2026-10-02."""

from django.db import migrations


PRICES = (
    (2135, "2032047000", 2630, 2628),
    (2123, "8114010U8520", 1920, 1919),
    (1988, "T151109111", 1660, 1599),
)


def set_prices(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    alias = schema_editor.connection.alias
    updates = []

    for product_id, article, old_price, new_price in PRICES:
        product = Product.objects.using(alias).select_for_update().filter(
            id=product_id,
            article=article,
        ).first()
        if product is None:
            if not Product.objects.using(alias).exists():
                return
            raise RuntimeError(f"Missing exact product {product_id} / {article}")
        if product.price == new_price:
            continue
        if product.price != old_price:
            raise RuntimeError(
                f"Unexpected website price for {article}: {product.price}"
            )
        updates.append((product_id, article, new_price))

    for product_id, article, new_price in updates:
        Product.objects.using(alias).filter(
            id=product_id,
            article=article,
        ).update(price=new_price)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0056_sync_151000025aa_price_20261002")]

    operations = [migrations.RunPython(set_prices, migrations.RunPython.noop)]
