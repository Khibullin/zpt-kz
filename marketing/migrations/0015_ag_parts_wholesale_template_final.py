from django.db import migrations


TEMPLATE_META_NAME = 'zpt_ag_parts_wholesale_v1'
TEMPLATE_LANGUAGE = 'ru'
NEW_HEADER = 'Оптовые автозапчасти AG Parts'
NEW_FOOTER = 'AG Parts / ZPT.KZ'
WHOLESALE_LAUNCH_URL = (
    'https://zpt.kz/seller/ag-parts/wholesale/'
    '?utm_source=whatsapp'
    '&utm_medium=marketing'
    '&utm_campaign=ag_parts_wholesale_launch_202608'
)
NEW_BUTTONS = [
    {
        'type': 'url',
        'text': 'Оптовые цены',
        'value': WHOLESALE_LAUNCH_URL,
    },
]
NEW_BODY = (
    'AG Parts — оптовые поставки автозапчастей для китайских автомобилей.\n'
    'Салонные фильтры — от 310 ₸/шт.\n'
    'Масляные фильтры — от 370 ₸/шт.\n'
    'Свечи зажигания — от 740 ₸/шт.\n'
    'Минимальный оптовый заказ — 10 единиц в ассортименте. Можно выбирать разные артикулы и марки.\n'
    'Все оптовые цены указаны с НДС.\n'
    'Самовывоз — Алматы. Доставка по Казахстану.\n'
    'Посмотрите ассортимент и актуальные оптовые цены на ZPT.KZ.'
)


def update_ag_parts_wholesale_template_final(apps, schema_editor):
    MarketingWhatsAppTemplate = apps.get_model(
        'marketing',
        'MarketingWhatsAppTemplate',
    )
    template = MarketingWhatsAppTemplate.objects.filter(
        meta_template_name=TEMPLATE_META_NAME,
        language_code=TEMPLATE_LANGUAGE,
    ).first()
    if template is None:
        return
    if template.meta_status == 'approved':
        return

    template.header_text = NEW_HEADER
    template.body_text = NEW_BODY
    template.footer_text = NEW_FOOTER
    template.buttons = list(NEW_BUTTONS)
    template.save(update_fields=['header_text', 'body_text', 'footer_text', 'buttons'])


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0014_ag_parts_wholesale_template_body_line'),
    ]

    operations = [
        migrations.RunPython(
            update_ag_parts_wholesale_template_final,
            migrations.RunPython.noop,
        ),
    ]
