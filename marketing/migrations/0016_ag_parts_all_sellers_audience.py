from django.db import migrations


TEMPLATE_META_NAME = 'zpt_ag_parts_wholesale_v1'
TEMPLATE_LANGUAGE = 'ru'
OLD_AUDIENCE_NAME = 'AG Parts — продавцы по маркам оптового ассортимента — 08.2026'
NEW_AUDIENCE_NAME = 'AG Parts — Китай или все марки — 08.2026'
CAMPAIGN_NAME = 'AG Parts — запуск оптовой витрины — 08.2026'
PURPOSE_ALL_SELLERS = 'all_sellers'


def retarget_ag_parts_all_sellers_audience(apps, schema_editor):
    MarketingWhatsAppTemplate = apps.get_model('marketing', 'MarketingWhatsAppTemplate')
    MarketingAudience = apps.get_model('marketing', 'MarketingAudience')
    MarketingCampaign = apps.get_model('marketing', 'MarketingCampaign')

    template = MarketingWhatsAppTemplate.objects.filter(
        meta_template_name=TEMPLATE_META_NAME,
        language_code=TEMPLATE_LANGUAGE,
    ).first()
    if template is not None:
        purposes = list(template.allowed_purposes or [])
        if PURPOSE_ALL_SELLERS not in purposes:
            purposes.append(PURPOSE_ALL_SELLERS)
            template.allowed_purposes = purposes
            template.save(update_fields=['allowed_purposes'])

    audience, _created = MarketingAudience.objects.update_or_create(
        name=NEW_AUDIENCE_NAME,
        defaults={
            'contact_group': 'sellers',
            'contact_subtype': 'all_sellers',
            'description': (
                'Все продавцы запчастей ZPT.KZ: выбранная страна производителя '
                '«Китай» или признак «все марки». Без ограничения по отдельным брендам. '
                'all_countries сам по себе не включает продавца.'
            ),
            'criteria': {
                'seller_countries': ['Китай'],
                'seller_include_all_brands': True,
                'is_active': True,
                'is_test': False,
            },
            'is_active': True,
        },
    )

    MarketingCampaign.objects.filter(name=CAMPAIGN_NAME).update(
        audience_id=audience.pk,
        purpose=PURPOSE_ALL_SELLERS,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0015_ag_parts_wholesale_template_final'),
    ]

    operations = [
        migrations.RunPython(
            retarget_ag_parts_all_sellers_audience,
            migrations.RunPython.noop,
        ),
    ]
