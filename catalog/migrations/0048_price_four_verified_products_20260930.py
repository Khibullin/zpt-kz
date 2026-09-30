"""Set retail prices for four verified AG Parts products.

Prices follow the rule ZPT = Kaspi target price. Only exact Product.price values
are changed; fitment, stock, images, slugs, and seller linkage are untouched.
"""
from django.db import migrations

PRICES = {
    "1056022300": {"id": 2174, "price": 1999},
    "8126100U851025": {"id": 2175, "price": 1590},
    "8126100U1510-06": {"id": 2176, "price": 1590},
    "F188107041": {"id": 2177, "price": 1438},
}

def forwards(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    alias = schema_editor.connection.alias
    rows = list(
        Product.objects.using(alias)
        .select_for_update()
        .filter(id__in=[v["id"] for v in PRICES.values()])
    )
    if not rows and not Product.objects.using(alias).exists():
        return
    if len(rows) != len(PRICES):
        raise RuntimeError("four-product pricing: missing product")
    by_id = {p.id: p for p in rows}
    for article, data in PRICES.items():
        p = by_id.get(data["id"])
        if p is None or p.article != article:
            raise RuntimeError(f"four-product pricing: identity drift for {article}")
    for article, data in PRICES.items():
        Product.objects.using(alias).filter(
            id=data["id"], article=article
        ).update(price=data["price"], price_on_request=False)

class Migration(migrations.Migration):
    dependencies = [("catalog", "0047_activate_four_verified_products_20260930")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
