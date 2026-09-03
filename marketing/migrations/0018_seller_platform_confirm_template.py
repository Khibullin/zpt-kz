from django.db import migrations


TEMPLATE_META_NAME = 'zpt_seller_platform_confirm_v1'
TEMPLATE_LANGUAGE = 'ru'
TEMPLATE_NAME = 'ZPT.KZ — подтверждение регистрации продавца'
TEMPLATE_HEADER = 'ZPT.KZ — маркетплейс для продавцов автозапчастей'
TEMPLATE_FOOTER = ''
TEMPLATE_BODY = '\n'.join([
    'Уважаемый продавец! Вы зарегистрированы на ZPT.KZ и получаете заявки покупателей на автозапчасти со всего Казахстана.',
    'Возможности для зарегистрированных продавцов',
    'Заявки покупателей',
    'Просматривайте поступившие заявки:',
    'https://zpt.kz/go/requests/',
    'Размещение товаров',
    'Добавляйте товары вручную или с ИИ-помощником. По артикулу система поможет заполнить описание, применимость, двигатели, OEM/кросс-номера и предложит фотографии:',
    'https://zpt.kz/go/add-product/',
    'Оптовые товары и прайс-листы:',
    'https://zpt.kz/go/wholesale/',
    'Каталог продавцов:',
    'https://zpt.kz/go/sellers/',
    'Вопросы и справки текстом или голосом:',
    'https://zpt.kz/go/help/',
    'Подтвердите, что хотите продолжать получать заявки и пользоваться возможностями ZPT.KZ.',
])
TEMPLATE_BUTTONS = [
    {
        'type': 'quick_reply',
        'text': 'Да, подтверждаю',
        'value': 'seller_confirm_yes',
    },
    {
        'type': 'quick_reply',
        'text': 'Нет, отключить',
        'value': 'seller_confirm_no',
    },
]
TEMPLATE_ALLOWED_PURPOSES = [
    'request_sellers',
    'marketplace_sellers',
    'combined_sellers',
    'all_sellers',
]
STABLE_GO_LINKS = (
    'https://zpt.kz/go/requests/',
    'https://zpt.kz/go/add-product/',
    'https://zpt.kz/go/wholesale/',
    'https://zpt.kz/go/sellers/',
    'https://zpt.kz/go/help/',
)
PROTECTED_META_STATUSES = frozenset({'approved', 'pending'})
EDITABLE_META_STATUSES = frozenset({'unknown', 'draft'})
MAX_HEADER_LENGTH = 60
MAX_BODY_LENGTH = 1024


def _template_fields():
    return {
        'name': TEMPLATE_NAME,
        'category': 'marketing',
        'meta_status': 'unknown',
        'is_active': True,
        'allow_test_campaign': True,
        'allowed_purposes': list(TEMPLATE_ALLOWED_PURPOSES),
        'header_text': TEMPLATE_HEADER,
        'body_text': TEMPLATE_BODY,
        'footer_text': TEMPLATE_FOOTER,
        'variables': [],
        'buttons': [dict(button) for button in TEMPLATE_BUTTONS],
        'internal_notes': (
            'Подготовлен локально для ручной отправки в Meta через admin action '
            '«Отправить в Meta». Миграция и деплой не обращаются к Meta Graph API.'
        ),
    }


def _has_been_submitted(template) -> bool:
    return bool(str(getattr(template, 'meta_template_id', '') or '').strip())


def seed_seller_platform_confirm_template(apps, schema_editor):
    MarketingWhatsAppTemplate = apps.get_model('marketing', 'MarketingWhatsAppTemplate')
    existing = MarketingWhatsAppTemplate.objects.filter(
        meta_template_name=TEMPLATE_META_NAME,
        language_code=TEMPLATE_LANGUAGE,
    ).first()
    fields = _template_fields()
    if existing is None:
        MarketingWhatsAppTemplate.objects.create(
            meta_template_name=TEMPLATE_META_NAME,
            language_code=TEMPLATE_LANGUAGE,
            **fields,
        )
        return
    if existing.meta_status in PROTECTED_META_STATUSES:
        return
    if existing.meta_status not in EDITABLE_META_STATUSES:
        return
    if _has_been_submitted(existing):
        return
    for field_name, value in fields.items():
        setattr(existing, field_name, value)
    existing.save()


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0017_alter_marketingcampaign_purpose'),
    ]

    operations = [
        migrations.RunPython(
            seed_seller_platform_confirm_template,
            migrations.RunPython.noop,
        ),
    ]
