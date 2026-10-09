"""Publish one narrowly reviewed article using a manufacturer's source.

The Chery 480-1012010 draft intentionally remains unpublished because
seller fitment claims disagree between third-party listings.
No changes occur if an editor already changed/reviewed the target article.
"""
from django.db import migrations
from django.utils import timezone

SLUG = "part-guide-2"

BODY = """Масляный фильтр MANN-FILTER HU 711/51 x: как проверить перед покупкой

HU 711/51 x — картриджный масляный фильтр MANN-FILTER. Он устанавливается в соответствующий корпус фильтра двигателя. Перед покупкой нужно убедиться, что именно такой элемент предусмотрен для вашего автомобиля.

Характеристики по каталогу MANN-FILTER:
— наружный диаметр: 64 мм;
— внутренний диаметр: 29 мм;
— высота: 69 мм;
— комплектуется уплотнением.

Для каких автомобилей указан HU 711/51 x?

В официальном каталоге MANN-FILTER среди основных применений указаны Peugeot 207, 308 и 308 II, отдельные модели Citroën (включая C4 Picasso и C5 II), MINI Cooper II и Ford Transit 2007. Это обзор семейств автомобилей, а не разрешение устанавливать фильтр на все моторы и все годы этих моделей.

Как проверить совместимость:
1. Узнайте двигатель, год выпуска и модификацию автомобиля.
2. Сверьте номер детали в каталоге MANN-FILTER для вашей конкретной версии автомобиля либо по VIN в подходящем каталоге.
3. Сравните размеры, конструкцию картриджа и уплотнительное кольцо с установленным фильтром.
4. Если номер не подтверждён, запросите у продавца уточнение применяемости до заказа.

Важно: обозначения 1109AH, 1109AJ и 1109CK встречаются в кросс-каталогах, но один только кросс-номер без данных о двигателе не доказывает взаимозаменяемость. HU 711/51 x — картридж, его нельзя путать с накручиваемыми фильтрами других конструкций.

Источник для технических данных и основных применений:
https://www.mann-filter.com/mea-en/catalogue/search-results/product.html/hu711/51x_mann-filter.html

На ZPT.KZ можно посмотреть предложение по этому артикулу. Если детали для вашего двигателя нет или совместимость вызывает сомнение — оставьте заявку на подбор продавцам. Перед оплатой подтвердите применяемость к вашему автомобилю."""


def publish_reviewed_peugeot(apps, schema_editor):
    Page = apps.get_model("core", "EditorialPage")
    Candidate = apps.get_model("core", "EditorialCandidate")
    candidate = Candidate.objects.filter(source_key="product:1981", source_product_id=1981).first()
    if candidate is None:
        return
    page = Page.objects.filter(
        slug=SLUG, source_candidate_id=candidate.id, status="draft",
        title__icontains="Peugeot 308",
        body__contains="По описанию продавца",
    ).first()
    if page is None:
        return
    Page.objects.filter(pk=page.pk, status="draft").update(
        title="Масляный фильтр Peugeot 308: как подобрать MANN HU 711/51 x",
        seo_title="MANN HU 711/51 x для Peugeot 308 — подбор и размеры | ZPT.KZ",
        meta_description="Характеристики фильтра MANN HU 711/51 x, список моделей из каталога производителя и проверка совместимости перед покупкой на ZPT.KZ.",
        body=BODY,
        status="published",
        published_at=timezone.now(),
        updated_at=timezone.now(),
    )


class Migration(migrations.Migration):
    dependencies = [("core", "0077_editorialdailyexecution")]
    operations = [migrations.RunPython(publish_reviewed_peugeot, migrations.RunPython.noop)]
