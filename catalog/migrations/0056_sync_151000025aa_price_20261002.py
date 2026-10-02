"""Align zpt.kz with the Kaspi price observed on 2026-10-02."""

from django.db import migrations


PRODUCT_ID = 1983
ARTICLE = "151000025AA"
OLD_PRICE = 930
NEW_PRICE = 928


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
    if product.price != OLD_PRICE:
        raise RuntimeError(
            f"Unexpected website price for {ARTICLE}: {product.price}"
        )
    Product.objects.using(alias).filter(
        id=PRODUCT_ID,
        article=ARTICLE,
    ).update(price=NEW_PRICE)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0055_haval_h9_maintenance_kit")]

    operations = [migrations.RunPython(set_price, migrations.RunPython.noop)]
