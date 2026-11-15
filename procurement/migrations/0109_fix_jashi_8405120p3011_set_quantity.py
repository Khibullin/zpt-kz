from django.db import migrations


OLD_DESCRIPTION = (
    "Новая автозапчасть. Совместимость уточняйте перед покупкой.\n\n"
    "Производитель: JASHI. Артикул: 8405120P3011/SET/6p/.\n"
    "Количество в цене: 1 шт.\n"
    "На складе поставщика. Наличие подтверждаем перед оплатой."
)
NEW_DESCRIPTION = OLD_DESCRIPTION.replace(
    "Количество в цене: 1 шт.",
    "Количество в цене: комплект 6 шт.",
)


def fix_exact_set_quantity(apps, schema_editor):
    """Correct only the confirmed package quantity; leave all trade data untouched."""
    if schema_editor.connection.alias != "default":
        return

    Product = apps.get_model("catalog", "Product")
    Activity = apps.get_model("procurement", "Activity")

    updated = Product.objects.filter(
        pk=9237,
        article="8405120P3011/SET/6p/",
        title="Комплект крепления JAC",
        description=OLD_DESCRIPTION,
        price=22620,
        cost_price=17400,
        stock_qty=2,
    ).update(description=NEW_DESCRIPTION)

    Activity.objects.create(
        user_id=None,
        action="reviewed_weekend_jashi_8405120p3011_set_quantity_corrected",
        details={
            "batch": "weekend-20261115-n",
            "offer_id": 28110,
            "product_id": 9237,
            "rows_updated": updated,
            "package_quantity": 6,
            "trade_data_changed": False,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("procurement", "0108_stage_jashi_8405120p3011_set_photo_fitment")]
    operations = [
        migrations.RunPython(fix_exact_set_quantity, migrations.RunPython.noop),
    ]
