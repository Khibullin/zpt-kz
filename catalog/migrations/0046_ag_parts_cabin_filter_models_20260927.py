"""Correct two AG Parts cabin-filter cards with unrelated Tiggo 7 model."""
from django.db import migrations

CHANGES = {
    "A138107915": {
        "id": 1986,
        "old_title": "Салонный фильтр",
        "old_model": 465,
        "old_compatibility": "рименимость:\nПодходит для следующих моделей автомобилей Chery:\n● Chery A13 (Very, Bonus, Forza)\n● Chery Tiggo 2\n● Chery QQ6\nЦена 2990 тг",
        "title": "Салонный фильтр Chery A13 / Tiggo 2 — A138107915",
        "compatibility": "Chery A13 (Bonus/Very/Forza) и Tiggo 2. OEM A13-8107915. Chery Tiggo 7 и QQ6 этим номером не подтверждаем. Перед заказом сверьте форму и размеры фильтра или VIN.",
        "oem_cross_references": "A138107915\nA13-8107915",
    },
    "M118107915": {
        "id": 1987,
        "old_title": "Салонный фильтр",
        "old_model": 465,
        "old_compatibility": "Применимость:\nПодходит для следующих моделей автомобилей Chery:\n● Chery M11\n● Chery M12\nЦена 1990 тг",
        "title": "Салонный фильтр Chery M11 / M12 — M118107915",
        "compatibility": "Chery M11 / M12. OEM M11-8107915. Chery Tiggo 7 этим номером не подтверждаем. Перед заказом сверьте форму и размеры фильтра или VIN.",
        "oem_cross_references": "M118107915\nM11-8107915",
    },
}

def forwards(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    alias = schema_editor.connection.alias
    products = list(Product.objects.using(alias).select_for_update().filter(
        id__in=[v["id"] for v in CHANGES.values()]
    ))
    if not products and not Product.objects.using(alias).exists():
        return
    if len(products) != len(CHANGES):
        raise RuntimeError("AG Parts cabin filters: missing product")
    by_id = {p.id: p for p in products}
    for article, change in CHANGES.items():
        p = by_id[change["id"]]
        if (
            p.article != article or p.seller_name != "AG Parts"
            or p.title != change["old_title"]
            or p.car_model_id != change["old_model"]
            or p.compatibility != change["old_compatibility"]
            or p.oem_cross_references != ""
        ):
            raise RuntimeError(f"AG Parts cabin filters: {article} drift")
    for article, change in CHANGES.items():
        p = by_id[change["id"]]
        Product.objects.using(alias).filter(id=p.id, article=article).update(
            title=change["title"],
            compatibility=change["compatibility"],
            oem_cross_references=change["oem_cross_references"],
            car_model_id=None,
        )

class Migration(migrations.Migration):
    dependencies = [("catalog", "0045_align_four_kaspi_prices_20260927")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
