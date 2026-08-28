from django.db import migrations


TEMPLATE_META_NAME = 'zpt_ag_parts_wholesale_v1'
TEMPLATE_LANGUAGE = 'ru'
TEMPLATE_NAME = 'AG Parts — оптовая витрина для продавцов'
AUDIENCE_NAME = 'AG Parts — продавцы по маркам оптового ассортимента — 08.2026'
CAMPAIGN_NAME = 'AG Parts — запуск оптовой витрины — 08.2026'

WHOLESALE_LAUNCH_URL = (
    'https://zpt.kz/seller/ag-parts/wholesale/'
    '?utm_source=whatsapp'
    '&utm_medium=marketing'
    '&utm_campaign=ag_parts_wholesale_launch_202608'
)

TEMPLATE_BODY = (
    'AG Parts — оптовые поставки автокомпонентов для магазинов, продавцов и СТО.\n'
    'Салонные фильтры — от 310 ₸/шт.\n'
    'Масляные фильтры — от 370 ₸/шт.\n'
    'Свечи зажигания — 740 ₸/шт.\n'
    'Минимальный оптовый заказ — 10 единиц в ассортименте. Можно выбирать разные артикулы и марки.\n'
    'Все оптовые цены указаны с НДС.\n'
    '100% предоплата после подтверждения наличия.\n'
    'Самовывоз — Алматы.\n'
    'Доставка по Казахстану — DPD, стоимость доставки оплачивает покупатель.\n'
    'Посмотрите ассортимент и актуальные оптовые цены на ZPT.KZ.'
)

AUDIENCE_BRANDS = [
    'BYD',
    'Changan',
    'Chery',
    'Exeed',
    'Great Wall',
    'Haval',
    'JAC',
    'Li Auto',
    'Nissan',
    'Peugeot',
    'Zeekr',
]


def prepare_ag_parts_wholesale_launch(apps, schema_editor):
    MarketingWhatsAppTemplate = apps.get_model('marketing', 'MarketingWhatsAppTemplate')
    MarketingAudience = apps.get_model('marketing', 'MarketingAudience')
    MarketingCampaign = apps.get_model('marketing', 'MarketingCampaign')

    template, _created_template = MarketingWhatsAppTemplate.objects.update_or_create(
        meta_template_name=TEMPLATE_META_NAME,
        language_code=TEMPLATE_LANGUAGE,
        defaults={
            'name': TEMPLATE_NAME,
            'category': 'marketing',
            'meta_status': 'unknown',
            'is_active': True,
            'allowed_purposes': [
                'request_sellers',
                'marketplace_sellers',
                'combined_sellers',
            ],
            'allow_test_campaign': True,
            'header_text': 'Оптовые автокомпоненты AG Parts',
            'body_text': TEMPLATE_BODY,
            'footer_text': 'AG Parts / ZPT.KZ',
            'variables': [],
            'buttons': [
                {
                    'type': 'url',
                    'text': 'Оптовые цены',
                    'value': WHOLESALE_LAUNCH_URL,
                },
            ],
            'meta_template_id': '',
            'internal_notes': (
                'Подготовлен для первой оптовой кампании AG Parts. '
                'Перед использованием создать идентичный MARKETING template в Meta '
                'и дождаться APPROVED. Не менять meta_status локально до фактического '
                'одобрения Meta.'
            ),
        },
    )

    audience, _created_audience = MarketingAudience.objects.update_or_create(
        name=AUDIENCE_NAME,
        defaults={
            'contact_group': 'sellers',
            'contact_subtype': 'all_sellers',
            'description': (
                'Продавцы ZPT.KZ, работающие с марками, представленными '
                'в первой оптовой витрине AG Parts.'
            ),
            'criteria': {
                'brands': list(AUDIENCE_BRANDS),
                'is_active': True,
                'is_test': False,
            },
            'is_active': True,
        },
    )

    campaign_fields = {
        'audience_id': audience.pk,
        'purpose': 'combined_sellers',
        'channel': 'whatsapp',
        'status': 'draft',
        'is_active': True,
        'description': (
            'Первая B2B-кампания постоянной оптовой витрины AG Parts на ZPT.KZ. '
            'UTM campaign: ag_parts_wholesale_launch_202608.'
        ),
        'message_template_id': template.pk,
    }
    updated = MarketingCampaign.objects.filter(name=CAMPAIGN_NAME).update(**campaign_fields)
    if not updated:
        MarketingCampaign.objects.bulk_create([
            MarketingCampaign(name=CAMPAIGN_NAME, **campaign_fields),
        ])


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0011_marketing_admin_verbose_names'),
    ]

    operations = [
        migrations.RunPython(
            prepare_ag_parts_wholesale_launch,
            migrations.RunPython.noop,
        ),
    ]
