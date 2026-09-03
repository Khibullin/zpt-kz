from django.db import migrations


TEMPLATE_META_NAME = 'zpt_seller_platform_confirm_v1'
TEMPLATE_LANGUAGE = 'ru'
EDITABLE_META_STATUSES = frozenset({'unknown', 'draft'})
MAX_BODY_LENGTH = 1024
EXPECTED_BODY_LENGTH = 1019
TEMPLATE_BODY = (
    'Уважаемый продавец!\n'
    '\n'
    'Вы зарегистрированы на ZPT.KZ и получаете заявки покупателей на автозапчасти со всего Казахстана.\n'
    '\n'
    'Возможности для зарегистрированных продавцов\n'
    '\n'
    'Получение заявок покупателей на ваши запчасти\n'
    'Просматривайте поступившие заявки в личном кабинете продавца.\n'
    'https://zpt.kz/go/requests/\n'
    '\n'
    'Размещение товаров на ZPT.KZ\n'
    'Добавляйте товары вручную или с помощью ИИ-помощника. Достаточно указать артикул — система поможет заполнить описание, применимость, двигатели, OEM/кросс-номера и предложит подходящие фотографии.\n'
    'https://zpt.kz/go/add-product/\n'
    '\n'
    'Оптовые товары и актуальные прайс-листы\n'
    'Просматривайте оптовые предложения и актуальные прайс-листы.\n'
    'https://zpt.kz/go/wholesale/\n'
    '\n'
    'Каталог продавцов ZPT.KZ\n'
    'Находите продавцов и поставщиков автозапчастей по всему Казахстану.\n'
    'https://zpt.kz/go/sellers/\n'
    '\n'
    'Вопросы и справки\n'
    'Задавайте вопросы текстом или голосом и получайте помощь по работе с ZPT.KZ.\n'
    'https://zpt.kz/go/help/\n'
    '\n'
    'Подтвердите, что хотите продолжать получать заявки и пользоваться возможностями ZPT.KZ:'
)


def _has_been_submitted(template) -> bool:
    return bool(str(getattr(template, 'meta_template_id', '') or '').strip())


def restore_full_seller_platform_confirm_body(apps, schema_editor):
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
        ('marketing', '0020_format_seller_platform_confirm_body'),
    ]

    operations = [
        migrations.RunPython(
            restore_full_seller_platform_confirm_body,
            migrations.RunPython.noop,
        ),
    ]
