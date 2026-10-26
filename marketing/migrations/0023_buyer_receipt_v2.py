from django.db import migrations

BODY = 'Ваша заявка №{{1}} принята в работу на ZPT.KZ.\n\nДополнительно вы можете выбрать до 10 продавцов по вашей заявке и написать им напрямую, чтобы уточнить наличие и цены. Готовый текст сообщения мы уже подготовили — вы сможете проверить и дополнить его перед отправкой.\n\nВыбрать продавцов: {{2}}\n\nСохраните это сообщение, чтобы в любое время вернуться к своей заявке.'

def seed(apps, schema_editor):
    Template = apps.get_model('marketing', 'MarketingWhatsAppTemplate')
    Template.objects.get_or_create(meta_template_name='zpt_customer_request_receipt_v1', language_code='ru', defaults={
        'name': 'ZPT.KZ — подтверждение заявки покупателю v2', 'category': 'marketing',
        'meta_status': 'unknown', 'is_active': True, 'body_text': BODY,
        'variables': [
            {'key': 'request_id', 'label': 'Номер заявки', 'required': True, 'example': '530'},
            {'key': 'request_url', 'label': 'Персональная ссылка', 'required': True, 'example': 'https://zpt.kz/connect/buyer/530/00000000-0000-0000-0000-000000000000/step/sellers/'}],
        'buttons': [], 'allowed_purposes': [], 'allow_test_campaign': False,
        'internal_notes': 'Текст согласован владельцем 26.10.2026. Только подтверждение новой заявки покупателю; без рассылки продавцам. До отправки требуется одобрение Meta и доступный бизнес-аккаунт.',
    })

class Migration(migrations.Migration):
    dependencies = [('marketing', '0022_seller_request_consent_template')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
