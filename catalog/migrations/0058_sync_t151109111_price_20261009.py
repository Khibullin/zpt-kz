"""Align zpt.kz with the Kaspi price observed on 2026-10-09."""

from django.db import migrations


PRODUCT_ID = 1988
ARTICLE = "T151109111"
EXPECTED_OLD_PRICE = 1599
NEW_PRICE = 1600


def set_price(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    alias = schema_editor.connection.alias
    product = Product.objects.using(alias).select_for_update().filter(
        id=PRODUCT_ID,
        article=ARTICLE,
    ).first()

    if product is None:
        if not Product.objects.using(alias).exists():
            return
        raise RuntimeError(f"Missing exact product {PRODUCT_ID} / {ARTICLE}")
    if product.price == NEW_PRICE:
        return
    if product.price != EXPECTED_OLD_PRICE:
        raise RuntimeError(
            f"Unexpected website price for {ARTICLE}: {product.price}"
        )

    Product.objects.using(alias).filter(
        id=PRODUCT_ID,
        article=ARTICLE,
    ).update(price=NEW_PRICE)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0057_sync_three_kaspi_prices_20261002")]

    operations = [migrations.RunPython(set_price, migrations.RunPython.noop)]
