"""Approved service notification contract; never substitutes a pending template."""
TEMPLATE_NAME = 'zpt_service_request_notification_v1'
TEMPLATE_BODY = (
    'Новая заявка №{{1}} на услуги\n\n'
    'Направление: {{2}}\nМарка: {{3}}\nМодель: {{4}}\n'
    'Работы и услуги: {{5}}\nГород: {{6}}\n\n'
    'Комментарий клиента:\n{{7}}\n\n'
    'Откройте заявку, чтобы посмотреть подробности и связаться с клиентом.\n'
    'Настройте фильтр, чтобы получать заявки по вашей специализации.\n\n'
    'Предложите клиенту стоимость и сроки выполнения работ.'
)
TEMPLATE_BUTTONS = [
    {'type': 'url', 'text': 'Открыть заявку',
     'value': 'https://zpt.kz/service-request/personal/{{1}}'},
    {'type': 'url', 'text': 'Настроить фильтр',
     'value': 'https://zpt.kz/service-request/personal/filters/{{1}}'},
]
SERVICE_LABELS = {'sto': 'СТО и ремонт', 'detailing': 'Детейлинг и тюнинг'}


def approved_service_template():
    from marketing.models import MarketingWhatsAppTemplate
    template = MarketingWhatsAppTemplate.objects.filter(
        meta_template_name=TEMPLATE_NAME, language_code='ru',
        meta_status='approved', is_active=True,
    ).first()
    return bool(template and template.body_text == TEMPLATE_BODY
                and template.buttons == TEMPLATE_BUTTONS)
