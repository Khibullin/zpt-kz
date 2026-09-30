"""Activate four verified AG Parts products and publish their fitment for Kaspi.

Only the four exact products are touched. Prices, stock, images, seller linkage,
slugs, and commercial terms are intentionally left unchanged.
"""

from django.db import migrations


PRODUCTS = {
    "1056022300": {
        "id": 2174,
        "brand": "Geely",
        "models": ("Coolray", "Atlas Pro", "Okavango"),
        "title": "Масляный фильтр Geely Coolray / Atlas Pro / Okavango — 1056022300",
        "compatibility": (
            "Geely Coolray (SX11) 1.5T, Atlas Pro 1.5T и Okavango 1.5 Hybrid. "
            "Точный OEM 1056022300 применяется с двигателями семейства JLH/JLE-3G15TD. "
            "Другие модели и моторы не считать подтверждёнными без сверки по VIN."
        ),
        "engine_compatibility": "JLH-3G15TD\nJLE-3G15TD",
        "oem_cross_references": "1056022300",
        "description": (
            "Масляный фильтр Geely 1056022300. Подтверждённая применяемость: "
            "Coolray 1.5T, Atlas Pro 1.5T и Okavango 1.5 Hybrid с двигателями "
            "семейства JLH/JLE-3G15TD. Перед заказом рекомендуется сверить OEM "
            "и VIN автомобиля."
        ),
    },
    "8126100U851025": {
        "id": 2175,
        "brand": "JAC",
        "models": ("J7", "JS4", "S3", "S3 Pro"),
        "title": "Салонный фильтр JAC J7 / JS4 / S3 / S3 Pro — 8126100U851025",
        "compatibility": (
            "JAC J7, JS4, S3 и S3 Pro. В официальном регламенте JAC Казахстан "
            "этот фильтр указан как 8126100U8510-25 для J7/JS4 и S3/S3 Pro. "
            "Салонный фильтр не привязан к конкретному двигателю внутри указанных моделей; "
            "перед заказом сверьте VIN и размеры."
        ),
        "engine_compatibility": "",
        "oem_cross_references": "8126100U851025\n8126100U8510-25",
        "description": (
            "Салонный фильтр JAC 8126100U851025 (формат OEM 8126100U8510-25). "
            "Подтверждён для JAC J7, JS4, S3 и S3 Pro. Применяемость салонного "
            "фильтра определяется кузовом и системой HVAC, а не кодом двигателя. "
            "Перед заказом сверьте VIN и размеры установленного фильтра."
        ),
    },
    "8126100U1510-06": {
        "id": 2176,
        "brand": "JAC",
        "models": ("S5", "JS5"),
        "title": "Салонный фильтр JAC S5 / JS5 — 8126100U1510-06",
        "compatibility": (
            "JAC S5 (2013–2022) и JAC JS5 (с 2024). Официальный регламент JAC Казахстан "
            "указывает OEM 8126100U1510-06 для JS5; каталоги точного OEM также подтверждают "
            "семейство S5. Салонный фильтр не привязан к конкретному двигателю внутри "
            "указанных моделей; перед заказом сверьте VIN и размеры."
        ),
        "engine_compatibility": "",
        "oem_cross_references": "8126100U1510-06\n8126100U151006",
        "description": (
            "Салонный фильтр JAC 8126100U1510-06 для S5 и JS5. Точный номер указан "
            "в регламенте JAC для JS5; для S5 подтверждается каталогами точного OEM. "
            "Применяемость салонного фильтра определяется кузовом и системой HVAC, "
            "а не кодом двигателя. Перед заказом сверьте VIN и размеры."
        ),
    },
    "F188107041": {
        "id": 2177,
        "brand": "Jetour",
        "models": ("X70 Plus", "X90 Plus", "Dashing", "T2"),
        "title": "Салонный фильтр Jetour X70 Plus / X90 Plus / Dashing / T2 — F188107041",
        "compatibility": (
            "Jetour X70 Plus (с 2020), X90 Plus (с 2021), Dashing (с 2022) и T2 (с 2023). "
            "Точный OEM также записывается как F18-8107041. X70 без Plus, X50 и Traveller "
            "не добавлять без отдельного подтверждения. Салонный фильтр не привязан к "
            "конкретному двигателю внутри указанных моделей; перед заказом сверьте VIN."
        ),
        "engine_compatibility": "",
        "oem_cross_references": "F188107041\nF18-8107041",
        "description": (
            "Салонный фильтр Jetour F188107041 / F18-8107041. Подтверждён для "
            "X70 Plus, X90 Plus, Dashing и T2. X70 без Plus, X50 и Traveller в эту "
            "карточку не включены без отдельного подтверждения. Перед заказом сверьте "
            "VIN и номер установленного фильтра."
        ),
    },
}


def forwards(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    Brand = apps.get_model("catalog", "Brand")
    CarModel = apps.get_model("catalog", "CarModel")
    alias = schema_editor.connection.alias

    ids = [item["id"] for item in PRODUCTS.values()]
    products = list(
        Product.objects.using(alias)
        .select_for_update()
        .filter(id__in=ids)
        .order_by("id")
    )
    if not products and not Product.objects.using(alias).exists():
        return
    if len(products) != len(PRODUCTS):
        raise RuntimeError("AG Parts four-product activation: one or more products are missing")

    by_id = {product.id: product for product in products}

    for article, data in PRODUCTS.items():
        product = by_id.get(data["id"])
        if (
            product is None
            or product.article != article
            or product.seller_name.casefold() != "ag parts"
        ):
            raise RuntimeError(
                f"AG Parts four-product activation: identity drift for {article}"
            )

    for article, data in PRODUCTS.items():
        product = by_id[data["id"]]

        brands = list(
            Brand.objects.using(alias).filter(name__iexact=data["brand"]).order_by("id")
        )
        if len(brands) != 1:
            raise RuntimeError(
                f"AG Parts four-product activation: expected one brand {data['brand']}"
            )
        brand = brands[0]

        model_rows = []
        for model_name in data["models"]:
            model = (
                CarModel.objects.using(alias)
                .filter(brand_id=brand.id, name__iexact=model_name)
                .order_by("id")
                .first()
            )
            if model is None:
                model = CarModel.objects.using(alias).create(
                    brand_id=brand.id,
                    name=model_name,
                )
            model_rows.append(model)

        Product.objects.using(alias).filter(id=product.id, article=article).update(
            title=data["title"],
            status="active",
            brand_id=brand.id,
            car_model_id=None,
            compatibility=data["compatibility"],
            engine_compatibility=data["engine_compatibility"],
            oem_cross_references=data["oem_cross_references"],
            description=data["description"],
            publish_to_kaspi=True,
        )

        product = Product.objects.using(alias).get(id=product.id)
        product.selected_brands.set([brand])
        product.selected_models.set(model_rows)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0046_ag_parts_cabin_filter_models_20260927")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
