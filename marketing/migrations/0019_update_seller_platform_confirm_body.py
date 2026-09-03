from django.db import migrations


TEMPLATE_META_NAME = 'zpt_seller_platform_confirm_v1'
TEMPLATE_LANGUAGE = 'ru'
EDITABLE_META_STATUSES = frozenset({'unknown', 'draft'})
MAX_BODY_LENGTH = 1024
STABLE_GO_LINKS = (
    'https://zpt.kz/go/requests/',
    'https://zpt.kz/go/add-product/',
    'https://zpt.kz/go/wholesale/',
    'https://zpt.kz/go/sellers/',
    'https://zpt.kz/go/help/',
)
TEMPLATE_BODY = '\n'.join([
    'Уважаемый продавец! Вы зарегистрированы на ZPT.KZ и получаете заявки покупателей на автозапчасти со всего Казахстана.',
    '',
    'Возможности для зарегистрированных продавцов:',
    'Просматривайте поступившие заявки покупателей:',
    'https://zpt.kz/go/requests/',
    '',
    'Размещайте товары вручную или с ИИ-помощником. По артикулу система поможет заполнить описание, применимость, двигатели, OEM/кросс-номера и предложит фотографии:',
    'https://zpt.kz/go/add-product/',
    '',
    'Оптовые товары и прайс-листы:',
    'https://zpt.kz/go/wholesale/',
    '',
    'Каталог продавцов:',
    'https://zpt.kz/go/sellers/',
    '',
    'Вопросы и справки текстом или голосом:',
    'https://zpt.kz/go/help/',
    '',
    'Подтвердите, что хотите продолжать получать заявки и пользоваться возможностями ZPT.KZ.',
])


def _has_been_submitted(template) -> bool:
    return bool(str(getattr(template, 'meta_template_id', '') or '').strip())


def update_seller_platform_confirm_body(apps, schema_editor):
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
        ('marketing', '0018_seller_platform_confirm_template'),
    ]

    operations = [
        migrations.RunPython(
            update_seller_platform_confirm_body,
            migrations.RunPython.noop,
        ),
    ]
