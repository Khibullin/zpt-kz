from django.db import migrations


def add_verified_kaspi_screen_details(apps, schema_editor):
    """User-provided screenshot of the Kaspi.kz product page, 21 Nov 2026.

    This is manual visual evidence, NOT a claim that HTTP extraction succeeded.
    Only this exact Kaspi card is updated; never prices or catalogue products.
    """
    Material = apps.get_model('procurement', 'BuyingComparisonMaterial')
    qs = Material.objects.filter(
        offer__brand__iexact='EEP',
        offer__article__iexact='15E130002',
        kaspi_product_id='151099201',
        kaspi_url__contains='-151099201/',
    )
    details = {
        'name': 'Диски EEP передние 15E130002',
        'brand': 'EEP',
        'article': '15E130002',
        'category': 'Тормозные диски',
        'fitment': 'Марка автомобиля: Ford, Mazda; Модель автомобиля: Tribute; Годы выпуска: 2000, 2001, 2002, 2003, 2004, 2005, 2006, 2007',
        'package': '1',
        'oem': 'E100-33-25XA',
        'specs': {'Назначение': 'передние'},
        'product_code': '151099201',
        'images': [],
        'url': 'https://kaspi.kz/shop/p/diski-eep-perednie-15e130002-151099201/',
        'source_section': 'Kaspi.kz — Характеристики',
        'extraction': 'user_supplied_screenshot_2026-11-21',
        'evidence_note': 'Проверено визуально по предоставленному владельцем снимку экрана карточки Kaspi; не автоматическое скачивание',
    }
    for record in qs:
        if record.kaspi_data:
            continue
        record.kaspi_data = details
        record.kaspi_status = 'manual_verified'
        record.kaspi_error = 'Автоматическое получение Kaspi возвращает HTTP 429; данные сверены по снимку страницы'
        record.save(update_fields=['kaspi_data', 'kaspi_status', 'kaspi_error', 'checked_at'])


class Migration(migrations.Migration):
    dependencies = [('procurement', '0128_buying_comparison_material')]
    operations = [migrations.RunPython(add_verified_kaspi_screen_details, migrations.RunPython.noop)]
