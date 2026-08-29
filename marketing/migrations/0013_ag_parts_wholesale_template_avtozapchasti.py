from django.db import migrations


TEMPLATE_META_NAME = 'zpt_ag_parts_wholesale_v1'
TEMPLATE_LANGUAGE = 'ru'
NEW_HEADER = 'Оптовые автозапчасти AG Parts'
NEW_FIRST_LINE = (
    'AG Parts — оптовые поставки автозапчастей для китайских автомобилей, '
    'магазинов, продавцов и СТО.'
)


def update_ag_parts_wholesale_template_copy(apps, schema_editor):
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

    body = template.body_text or ''
    lines = body.split('\n') if body else ['']
    lines[0] = NEW_FIRST_LINE
    template.header_text = NEW_HEADER
    template.body_text = '\n'.join(lines)
    template.save(update_fields=['header_text', 'body_text'])


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0012_prepare_ag_parts_wholesale_launch'),
    ]

    operations = [
        migrations.RunPython(
            update_ag_parts_wholesale_template_copy,
            migrations.RunPython.noop,
        ),
    ]
