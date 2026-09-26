"""Guarded AG Parts fitment clarification for three exact articles.

Only compatibility and description are written. No kit, price, stock, photo,
OEM, slug, or selected model is changed. All expected production values are
checked before any write.
"""
from django.db import migrations

BEFORE = {
    "1109140W5000": {
        "id": 2133,
        "slug": "1109140w5000",
        "title": "Воздушный фильтр 1109140W5000",
        "compatibility": "OEM 1109140W5000. Не смешивать с 28113-4F000. JAC N25, N35 и Sollers Argo этим артикулом не подтверждаем без сверки с установленной деталью или VIN.",
        "description": "Воздушный фильтр очищает воздух на впуске двигателя. OEM: 1109140W5000.\nНе смешивать с 28113-4F000.\nJAC N25, N35 и Sollers Argo этим артикулом не подтверждаем без сверки с установленной деталью или VIN автомобиля."
    },
    "301000265AA": {
        "id": 1984,
        "slug": "salonnyi-filtr-301000265aa",
        "title": "Салонный фильтр EXEED TXL / VX — 301000265AA",
        "compatibility": "EXEED TXL 1.6 SQRF4J16A (с 04.2019) и SQRF4J16D (с 07.2023), кузов M32T; EXEED TXL 2.0 SQRF4J20C; EXEED VX 2.0 SQRF4J20C, кузов M36T. Пылевой OEM 301000265AA. Не путать с угольным 301001199AA. Tiggo 8 Pro 1.6 для T21-8107011.",
        "description": "Салонный фильтр очищает воздух, поступающий в салон через систему вентиляции и кондиционирования. OEM: 301000265AA.\nОсновная применимость: EXEED TXL 1.6 SQRF4J16A / SQRF4J16D и 2.0 SQRF4J20C; EXEED VX 2.0 SQRF4J20C.\nПылевой вариант; не путать с угольным 301001199AA.\nTiggo 8 Pro 1.6 для T21-8107011.\nПеред заказом сверьте артикул с установленной деталью или VIN автомобиля."
    },
    "301001199AA": {
        "id": 1985,
        "slug": "salonnyi-filtr-301001199aa",
        "title": "Салонный фильтр EXEED TXL / VX — 301001199AA",
        "compatibility": "EXEED TXL 1.6 SQRF4J16A (с 04.2019) и SQRF4J16D (с 07.2023), кузов M32T; EXEED TXL 2.0 SQRF4J20C; EXEED VX 2.0 SQRF4J20C, кузов M36T. Угольный OEM 301001199AA / 301001199A. Tiggo 8 Pro 1.6 для T21-8107011; для 301001199AA Tiggo 8 Pro не подтверждаем. Не путать с пылевым 301000265AA.",
        "description": "Салонный фильтр очищает воздух, поступающий в салон через систему вентиляции и кондиционирования. OEM: 301001199AA; 301001199A.\nОсновная применимость: EXEED TXL 1.6 SQRF4J16A / SQRF4J16D и 2.0 SQRF4J20C; EXEED VX 2.0 SQRF4J20C.\nУгольный вариант; не путать с пылевым 301000265AA.\nTiggo 8 Pro 1.6 для T21-8107011; для 301001199AA Tiggo 8 Pro не подтверждаем.\nПеред заказом сверьте артикул с установленной деталью или VIN автомобиля."
    }
}
AFTER = {
    "1109140W5000": {
        "compatibility": "JAC N25/N35 (E5) и Sollers Argo 2.0: точный номер 1109140W5000 указан в каталоге производителя фильтров как кросс. Конкретную модификацию сверяйте по VIN и размерам установленного фильтра. 28113-4F000 — отдельный номер в общей группе аналога, не артикул этого товара.",
        "description": "Воздушный фильтр 1109140W5000. Независимый каталог производителя аналога связывает этот точный номер с JAC N25/N35 (E5) и Sollers Argo 2.0. Номер 28113-4F000 относится к другой упаковке в той же группе; не подменяйте им артикул товара. Перед заказом сверьте VIN и размеры установленного фильтра."
    },
    "301000265AA": {
        "compatibility": "EXEED TXL 1.6 SQRF4J16A (с 04.2019) и SQRF4J16D (с 07.2023), кузов M32T; EXEED TXL 2.0 SQRF4J20C; EXEED VX 2.0 SQRF4J20C, кузов M36T. OEM-кросс 301000265AA указан для TXL/VX в независимых каталогах фильтров. Номер 301001199AA — другой артикул той же посадочной группы; материал фильтра сверяйте по фактической упаковке. Tiggo 8 Pro 1.6 для T21-8107011.",
        "description": "Салонный фильтр 301000265AA для EXEED TXL/VX. Точный номер приведён в таблице применяемости независимого производителя фильтров. 301001199AA указан отдельно в каталогах той же посадочной группы; маркировку и материал конкретного фильтра проверяйте на упаковке. Tiggo 8 Pro 1.6 для T21-8107011. Перед заказом сверьте VIN, артикул и размеры установленной детали."
    },
    "301001199AA": {
        "compatibility": "EXEED TXL 1.6 SQRF4J16A (с 04.2019) и SQRF4J16D (с 07.2023), кузов M32T; EXEED TXL 2.0 SQRF4J20C; EXEED VX 2.0 SQRF4J20C, кузов M36T. Точный номер 301001199AA приведён как OEM-кросс для TXL/VX в независимом каталоге фильтров; фото упаковки подтверждает номер и тип, но не материал. 301000265AA — отдельный артикул той же посадочной группы. Tiggo 8 Pro этим номером не подтверждаем.",
        "description": "Салонный фильтр 301001199AA для EXEED TXL/VX. Фото упаковки подтверждает номер и тип; независимый каталог фильтров связывает точный OEM-кросс с этими моделями. 301000265AA указан отдельно в той же посадочной группе. Материал конкретного товара, в том числе наличие угольного слоя, по имеющимся источникам не установлен: сверьте упаковку и размеры перед заказом. Tiggo 8 Pro этим номером не подтверждаем."
    }
}

def forwards(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    alias = schema_editor.connection.alias
    products = list(Product.objects.using(alias).select_for_update().filter(article__in=BEFORE).order_by("article", "id"))
    if not products:
        return  # Fresh database without imported AG Parts products.
    if len(products) != len(BEFORE) or {p.article for p in products} != set(BEFORE):
        raise RuntimeError("AG Parts last eight: incomplete or duplicate exact-article set")
    by_article = {p.article: p for p in products}
    for article, expected in BEFORE.items():
        product = by_article[article]
        for field, value in expected.items():
            if getattr(product, field) != value:
                raise RuntimeError(f"AG Parts last eight: {article} {field} drift; no update applied")
    for article, changes in AFTER.items():
        product = by_article[article]
        for field, value in changes.items():
            setattr(product, field, value)
        product.save(using=alias, update_fields=list(changes))

def backwards(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    alias = schema_editor.connection.alias
    products = list(Product.objects.using(alias).select_for_update().filter(article__in=BEFORE).order_by("article", "id"))
    if not products:
        return
    if len(products) != len(BEFORE) or {p.article for p in products} != set(BEFORE):
        raise RuntimeError("AG Parts last eight reverse: incomplete exact-article set")
    by_article = {p.article: p for p in products}
    for article in BEFORE:
        product = by_article[article]
        if product.id != BEFORE[article]["id"] or product.slug != BEFORE[article]["slug"]:
            raise RuntimeError(f"AG Parts last eight reverse: {article} identity drift")
        for field, value in AFTER[article].items():
            if getattr(product, field) != value:
                raise RuntimeError(f"AG Parts last eight reverse: {article} {field} drift")
    for article, changes in AFTER.items():
        product = by_article[article]
        for field in changes:
            setattr(product, field, BEFORE[article][field])
        product.save(using=alias, update_fields=list(changes))

class Migration(migrations.Migration):
    dependencies = [("catalog", "0040_ag_parts_fitment_batch18")]
    operations = [migrations.RunPython(forwards, backwards)]
