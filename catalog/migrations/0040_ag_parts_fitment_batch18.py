"""One-time guarded correction of six verified AG Parts Product descriptions.

The production values were inspected read-only on 2026-09-26. A mismatch aborts
the transaction before any Product is saved. No kit or commercial field is written.
"""

from django.db import migrations


BEFORE = {
    "1064000180": {
        "id": 2139,
        "seller_name": "AG Parts",
        "status": "active",
        "slug": "1064000180",
        "title": "Воздушный фильтр 1064000180",
        "compatibility": "OEM 1064000180. Не смешивать с Lifan B1109130 и Toyota 17801. Модели и двигатели этим артикулом не подтверждаем без сверки с установленной деталью или VIN.",
        "engine_compatibility": "",
        "oem_cross_references": "1064000180",
        "description": "Воздушный фильтр очищает воздух на впуске двигателя. OEM: 1064000180.\nНе смешивать с Lifan B1109130 и Toyota 17801.\nМодели и двигатели этим артикулом не подтверждаем без сверки с установленной деталью или VIN автомобиля."
    },
    "151000151AA": {
        "id": 2134,
        "seller_name": "AG Parts",
        "status": "active",
        "slug": "151000151aa",
        "title": "Воздушный фильтр 151000151AA",
        "compatibility": "OEM 151000151AA. Не смешивать с 151000079AA (Tiggo 8 Pro 1.6) и T151109111. Модели и двигатели этим артикулом не подтверждаем без сверки с установленной деталью или VIN.",
        "engine_compatibility": "",
        "oem_cross_references": "151000151AA\nF02-1109111-03AA\n151000151AB",
        "description": "Воздушный фильтр очищает воздух на впуске двигателя. OEM: 151000151AA; F02-1109111-03AA; 151000151AB.\nНе смешивать с 151000079AA (Tiggo 8 Pro 1.6) и T151109111.\nМодели и двигатели этим артикулом не подтверждаем без сверки с установленной деталью или VIN автомобиля."
    },
    "EM2E-8121211E": {
        "id": 1990,
        "seller_name": "AG Parts",
        "status": "active",
        "slug": "byd-2",
        "title": "Салонный фильтр BYD — EM2E-8121211E",
        "compatibility": "OEM EM2E-8121211E. Не смешивать с SA2E-8121211E и 13033898-00. BYD Dolphin и Atto 3 этим артикулом не подтверждаем без сверки с установленной деталью или VIN. Song Plus этим артикулом не подтверждаем.",
        "oem_cross_references": "EM2E-8121211E\nEM2E8121211E",
        "description": "Салонный фильтр очищает воздух, поступающий в салон через систему вентиляции и кондиционирования. OEM: EM2E-8121211E / EM2E8121211E.\nНе смешивать с SA2E-8121211E и 13033898-00.\nBYD Dolphin и Atto 3 этим артикулом не подтверждаем без сверки с установленной деталью или VIN.\nSong Plus этим артикулом не подтверждаем.\nПеред заказом сверьте артикул с установленной деталью или VIN автомобиля."
    },
    "8890649934": {
        "id": 2109,
        "seller_name": "AG Parts",
        "status": "active",
        "slug": "zeekr-001-8890649934-zeekr-001",
        "title": "Салонный фильтр 8890649934",
        "compatibility": "OEM 8890649934. Zeekr 001 и 009 этим артикулом не подтверждаем без сверки с установленной деталью или VIN. Zeekr 007 и MIX этим артикулом не подтверждаем.",
        "oem_cross_references": "8890649934",
        "description": "Салонный фильтр очищает воздух, поступающий в салон через систему вентиляции и кондиционирования. OEM: 8890649934.\nZeekr 001 и 009 этим артикулом не подтверждаем без сверки с установленной деталью или VIN.\nZeekr 007 и MIX этим артикулом не подтверждаем.\nПеред заказом сверьте артикул с установленной деталью или VIN автомобиля."
    },
    "CD569F2801032700": {
        "id": 1994,
        "seller_name": "AG Parts",
        "status": "active",
        "slug": "changan-uni-k-changan",
        "title": "Салонный фильтр CD569F2801032700",
        "compatibility": "OEM CD569F2801032700. Не смешивать с CD569F280103-2701. Changan UNI-K этим артикулом не подтверждаем без сверки с установленной деталью или VIN.",
        "oem_cross_references": "CD569F2801032700\nCD569F280103-2700",
        "description": "Салонный фильтр очищает воздух, поступающий в салон через систему вентиляции и кондиционирования. OEM: CD569F2801032700 / CD569F280103-2700.\nНе смешивать с CD569F280103-2701.\nChangan UNI-K этим артикулом не подтверждаем без сверки с установленной деталью или VIN автомобиля."
    },
    "X01-90000014": {
        "id": 2128,
        "seller_name": "AG Parts",
        "status": "active",
        "slug": "li-auto-l7-x01-90000014-li-auto-l7",
        "title": "Воздушный фильтр X01-90000014",
        "compatibility": "OEM X01-90000014. Li Auto L6, L7, L8 и L9 этим артикулом не подтверждаем без сверки с установленной деталью или VIN. Это воздушный фильтр двигателя, не салонный. Не смешивать с X01-29150063.",
        "engine_compatibility": "",
        "oem_cross_references": "X01-90000014\nX0190000014",
        "description": "Воздушный фильтр очищает воздух на впуске двигателя. OEM: X01-90000014 / X0190000014.\nLi Auto L6, L7, L8 и L9 этим артикулом не подтверждаем без сверки с установленной деталью или VIN.\nЭто воздушный фильтр двигателя, не салонный.\nНе смешивать с X01-29150063.\nПеред заказом сверьте артикул с установленной деталью или VIN автомобиля."
    }
}

AFTER = {
    "1064000180": {
        "title": "Воздушный фильтр Geely Emgrand — 1064000180",
        "compatibility": "Номер 1064000180 подтверждён в каталоге запчастей Geely FC и как OEM-кросс для Emgrand FE-1 1.5 JL4G15 / 1.8 JL4G18 (2012–2017), Emgrand 7 FE-3JC 1.5 JLy4G15 / 1.8 JLy4G18 (2016–2020). Другие модели и годы требуют сверки по VIN. Не переносить применяемость BYD, Lifan, Toyota из общей таблицы аналога.",
        "engine_compatibility": "JL4G15\nJL4G18\nJLy4G15\nJLy4G18",
        "oem_cross_references": "1064000180",
        "description": "Воздушный фильтр двигателя Geely 1064000180. Для Emgrand FE-1 и Emgrand 7 FE-3JC указаны конкретные двигатели и периоды в независимом каталоге фильтров; каталог деталей Geely FC также содержит этот точный номер. Не считать подтверждёнными все годы и комплектации EC7/GC7/SL. Перед заказом сверьте номер установленного фильтра и VIN."
    },
    "151000151AA": {
        "title": "Воздушный фильтр Chery 2.0 — 151000151AA",
        "compatibility": "По каталогу точного OEM 151000151AA: Chery Tiggo 8 T1A 2.0 SQRF4J20 (07.2021–10.2023), Tiggo 8 Pro Max T1D 2.0 SQRF4J20 (с 05.2022), Tiggo 9 2.0 SQRF4J20C/A/B; EXEED RX 2.0 SQRF4J20C; JAECOO J8 2.0 SQRF4J20C; KAIYI X7 Kunlun 2.0 SQRF4J20; TENET T8 2.0 SQRF4J20B. Не переносить эту применяемость на все Tiggo 8 и на 1.6. Не смешивать с 151000079AA и T151109111.",
        "engine_compatibility": "SQRF4J20\nSQRF4J20A\nSQRF4J20B\nSQRF4J20C",
        "oem_cross_references": "151000151AA\nF02-1109111-03AA",
        "description": "Воздушный фильтр двигателя 151000151AA. Подтверждённые варианты: Tiggo 8 T1A 2.0 SQRF4J20 (07.2021–10.2023), Tiggo 8 Pro Max T1D 2.0 SQRF4J20 (с 05.2022), Tiggo 9 2.0 SQRF4J20C/A/B, EXEED RX 2.0 SQRF4J20C, JAECOO J8 2.0 SQRF4J20C, KAIYI X7 Kunlun 2.0 SQRF4J20, TENET T8 2.0 SQRF4J20B. Кросс KAIYI: F02-1109111-03AA. Не смешивать с 151000079AA и T151109111. Перед заказом сверьте двигатель, кузов и номер установленного фильтра."
    },
    "EM2E-8121211E": {
        "title": "Салонный фильтр BYD Atto 3 / Dolphin — EM2E-8121211E",
        "compatibility": "BYD Atto 3 (с 2022) и Dolphin (с 2023): номер EM2E-8121211E указан в каталоге производителя фильтров как OEM-кросс. Размер аналога 199 × 210 × 30 мм. Для BYD Song Plus EV применяемость этого номера пока не подтверждена. Не смешивать с SA2E-8121211E и 13033898-00.",
        "oem_cross_references": "EM2E-8121211E\nEM2E8121211E",
        "description": "Салонный фильтр BYD EM2E-8121211E. Независимый каталог фильтров указывает этот OEM-кросс для Atto 3 (с 2022) и Dolphin (с 2023). Размер указанного аналога 199 × 210 × 30 мм; размеры товара AG Parts сверяйте по упаковке. Применяемость к Song Plus EV этим источником не подтверждена. Не смешивать с SA2E-8121211E и 13033898-00. Перед заказом сверьте номер и размеры установленного фильтра."
    },
    "8890649934": {
        "title": "Салонный фильтр Zeekr 001 — 8890649934",
        "compatibility": "Каталог запчастей дилера указывает точный 8890649934 для Zeekr 001 и Zeekr 7X. Для Zeekr 009 есть кросс производителя фильтров к тому же OEM; перед заказом на 009 сверьте VIN и размеры. Для 007 и MIX применяемость не подтверждена. Не смешивать с 8890649934M.",
        "oem_cross_references": "8890649934",
        "description": "Салонный фильтр 8890649934. В каталоге запчастей дилера точный номер указан для Zeekr 001 и 7X. Для Zeekr 009 известен независимый кросс производителя аналога, поэтому необходима сверка по VIN и размерам. Для 007 и MIX применяемость не подтверждена. Номер 8890649934M — отдельный артикул."
    },
    "CD569F2801032700": {
        "title": "Салонный фильтр Changan UNI-K — CD569F2801032700",
        "compatibility": "Changan UNI-K (с 2020): точный номер CD569F2801032700 приведён в каталоге производителя фильтров как OEM-кросс салонного фильтра. Не смешивать с CD569F280103-2701. Перед заказом сверьте номер и размеры установленной детали.",
        "oem_cross_references": "CD569F2801032700\nCD569F280103-2700",
        "description": "Салонный фильтр CD569F2801032700 для Changan UNI-K (с 2020). Применяемость точного OEM подтверждена каталогом производителя фильтров. Не смешивать с другим номером CD569F280103-2701. Перед заказом сверьте номер и размеры установленного фильтра."
    },
    "X01-90000014": {
        "title": "Воздушный фильтр Li Auto L6 / L7 / L8 / L9 — X01-90000014",
        "compatibility": "Li Auto L6 (с 04.2024), L7/L8 (с 09.2022), L9 (с 06.2022), двигатель 1.5 L2E15M: точный X01-90000014 приведён как OEM-кросс в независимом каталоге фильтров. Для L7/L8/L9 точный номер есть также в каталоге деталей. Это воздушный фильтр двигателя, не салонный. X01-29150063 указан как отдельный OEM-кросс к одному аналогу; не считать номера одной и той же упаковкой.",
        "engine_compatibility": "L2E15M",
        "oem_cross_references": "X01-90000014\nX0190000014",
        "description": "Воздушный фильтр двигателя Li Auto X01-90000014 для L6, L7, L8, L9 с двигателем L2E15M. Каталог производителя аналога приводит точный OEM и модификации; каталог деталей дополнительно подтверждает L7/L8/L9. Не смешивать с салонным фильтром и не подменять отдельным номером X01-29150063 без сверки детали. Перед заказом сверьте VIN и номер установленного фильтра."
    }
}


def _apply(apps, schema_editor, source, destination):
    Product = apps.get_model("catalog", "Product")
    alias = schema_editor.connection.alias
    articles = tuple(source)
    products = list(Product.objects.using(alias).filter(article__in=articles).order_by("article", "id"))
    if not products:
        return  # Fresh database: these imported catalogue rows do not exist.
    if len(products) != len(articles):
        raise RuntimeError("AG Parts batch 18: incomplete or duplicate set of six exact articles")

    indexed = {product.article: product for product in products}
    if set(indexed) != set(articles):
        raise RuntimeError("AG Parts batch 18: article mismatch")
    # Validate every row before the first write. A database drift requires a new review.
    for article in articles:
        product = indexed[article]
        for field, value in source[article].items():
            if getattr(product, field) != value:
                raise RuntimeError(f"AG Parts batch 18: {article} field {field} changed; no update applied")

    for article in articles:
        product = indexed[article]
        fields = []
        for field, value in destination[article].items():
            if field in ("id", "seller_name", "status", "slug"):
                continue
            if getattr(product, field) != value:
                setattr(product, field, value)
                fields.append(field)
        if fields:
            product.save(using=alias, update_fields=fields)


def forwards(apps, schema_editor):
    _apply(apps, schema_editor, BEFORE, AFTER)


def backwards(apps, schema_editor):
    restore = {article: {field: BEFORE[article][field] for field in AFTER[article]} for article in BEFORE}
    expected = {
        article: {**{field: BEFORE[article][field] for field in ("id", "seller_name", "status", "slug")}, **AFTER[article]}
        for article in BEFORE
    }
    _apply(apps, schema_editor, expected, restore)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0039_maintenance_kit_cover_note_reference")]
    operations = [migrations.RunPython(forwards, backwards)]
