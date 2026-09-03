from django.db import migrations


TEMPLATE_META_NAME = 'zpt_seller_platform_confirm_v1'
TEMPLATE_LANGUAGE = 'ru'
EDITABLE_META_STATUSES = frozenset({'unknown', 'draft'})
MAX_BODY_LENGTH = 1024
TEMPLATE_BODY = (
    'Уважаемый продавец! Вы зарегистрированы на ZPT.KZ и получаете заявки покупателей на автозапчасти со всего Казахстана.\n'
    '\n'
    'Возможности для зарегистрированных продавцов:\n'
    'Просматривайте поступившие заявки покупателей:\n'
    'https://zpt.kz/go/requests/\n'
    '\n'
    'Размещайте товары вручную или с ИИ-помощником. По артикулу система поможет заполнить описание, применимость, двигатели, OEM/кросс-номера и предложит фотографии:\n'
    'https://zpt.kz/go/add-product/\n'
    '\n'
    'Оптовые товары и прайс-листы:\n'
    'https://zpt.kz/go/wholesale/\n'
    '\n'
    'Каталог продавцов:\n'
    'https://zpt.kz/go/sellers/\n'
    '\n'
    'Вопросы и справки текстом или голосом:\n'
    'https://zpt.kz/go/help/'
)


def _has_been_submitted(template) -> bool:
    return bool(str(getattr(template, 'meta_template_id', '') or '').strip())


def format_seller_platform_confirm_body(apps, schema_editor):
    MarketingWhatsAppTemplate = apps.get_model('marketing', 'MarketingWhatsAppTemplate')
    template = MarketingWhatsAppTemplate.objects.filter(
        meta_template_name=TEMPLATE_META_NAME,
        language_code=TEMPLATE_LANGUAGE,
    ).first()
    if template is None:
        return
    if template.meta_status not in EDITABLE_META_STATUSES:
        return
    if _has_been_submitted(template):
        return
    template.body_text = TEMPLATE_BODY
    template.save(update_fields=['body_text'])


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0019_update_seller_platform_confirm_body'),
    ]

    operations = [
        migrations.RunPython(
            format_seller_platform_confirm_body,
            migrations.RunPython.noop,
        ),
    ]
