from django.db import migrations


TEMPLATE_META_NAME = 'zpt_request_notification_consent_v1'
TEMPLATE_LANGUAGE = 'ru'
TEMPLATE_NAME = 'ZPT.KZ — заявка покупателя + согласие продавца'
TEMPLATE_BODY = (
    'Новая заявка №{{1}} от клиента на автозапчасть\n'
    '\n'
    'Марка: {{2}}\n'
    'Модель: {{3}}\n'
    'Категория: {{4}}\n'
    'Город: {{5}}\n'
    '\n'
    'Комментарий клиента:\n'
    '{{6}}\n'
    '\n'
    'Телефон клиента:\n'
    '{{7}}\n'
    '\n'
    'Пожалуйста, свяжитесь с клиентом и предложите наличие, цену и сроки поставки.\n'
    '\n'
    'Личный кабинет / просмотр заявок / отписаться:\n'
    'https://zpt.kz\n'
    '\n'
    'По всем вопросам:\n'
    'WhatsApp +7 771 360 7040\n'
    '\n'
    'Хотите получать от ZPT.KZ заявки покупателей и выгодные предложения?'
)
TEMPLATE_BUTTONS = [
    {
        'type': 'quick_reply',
        'text': 'Да, получать',
        'value': 'seller_confirm_yes',
    },
    {
        'type': 'quick_reply',
        'text': 'Отключить',
        'value': 'seller_confirm_no',
    },
]
TEMPLATE_VARIABLES = [
    {'key': 'request_id', 'label': 'Номер заявки', 'required': True, 'example': '431'},
    {'key': 'brand', 'label': 'Марка', 'required': True, 'example': 'Chery'},
    {'key': 'model', 'label': 'Модель', 'required': True, 'example': 'Tiggo 7 Pro'},
    {'key': 'category', 'label': 'Категория', 'required': True, 'example': 'Тормоза'},
    {'key': 'city', 'label': 'Город', 'required': True, 'example': 'Алматы'},
    {
        'key': 'description',
        'label': 'Комментарий и ссылка на заявку',
        'required': True,
        'example': 'Нужен пыльник на тормозной цилиндр задний | 🔗 Открыть заявку: https://zpt.kz/r/431/8micYM/',
    },
    {
        'key': 'client_phone',
        'label': 'Телефон клиента',
        'required': True,
        'example': '+7 775 813 4694',
    },
]
TEMPLATE_ALLOWED_PURPOSES = [
    'request_sellers',
    'combined_sellers',
    'all_sellers',
]


def seed_seller_request_consent_template(apps, schema_editor):
    MarketingWhatsAppTemplate = apps.get_model('marketing', 'MarketingWhatsAppTemplate')
    defaults = {
        'name': TEMPLATE_NAME,
        'category': 'marketing',
        'meta_status': 'unknown',
        'is_active': True,
        'allow_test_campaign': True,
        'allowed_purposes': list(TEMPLATE_ALLOWED_PURPOSES),
        'header_text': '',
        'body_text': TEMPLATE_BODY,
        'footer_text': '',
        'buttons': [dict(button) for button in TEMPLATE_BUTTONS],
        'variables': [dict(variable) for variable in TEMPLATE_VARIABLES],
        'internal_notes': (
            'Новый шаблон заявки продавцу с opt-in на заявки покупателей и выгодные '
            'предложения. До одобрения Meta не используется в боевой рассылке. '
            'Активация после APPROVED: WHATSAPP_SELLER_CONSENT_TEMPLATE_ENABLED=true.'
        ),
    }
    template, created = MarketingWhatsAppTemplate.objects.get_or_create(
        meta_template_name=TEMPLATE_META_NAME,
        language_code=TEMPLATE_LANGUAGE,
        defaults=defaults,
    )
    if created:
        return
    if template.meta_status not in {'unknown', 'draft'}:
        return
    if str(template.meta_template_id or '').strip():
        return
    for field_name, value in defaults.items():
        setattr(template, field_name, value)
    template.save()


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0021_restore_full_seller_platform_confirm_body'),
    ]

    operations = [
        migrations.RunPython(
            seed_seller_request_consent_template,
            migrations.RunPython.noop,
        ),
    ]
