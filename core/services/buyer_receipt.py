"""One buyer receipt per new request, independent of seller dispatch."""
from django.db import transaction
from django.urls import reverse
from core.models import Request, WhatsAppMessageLog
from core.whatsapp_template_sender import send_whatsapp_template_message, wa_template_param

TEMPLATE_NAME = 'zpt_customer_request_receipt_v1'
BODY = (
    'Ваша заявка №{{1}} принята в работу на ZPT.KZ.\n\n'
    'Дополнительно вы можете выбрать до 10 продавцов по вашей заявке и написать им напрямую, '
    'чтобы уточнить наличие и цены. Готовый текст сообщения мы уже подготовили — вы сможете '
    'проверить и дополнить его перед отправкой.\n\n'
    'Выбрать продавцов: {{2}}\n\n'
    'Сохраните это сообщение, чтобы в любое время вернуться к своей заявке.'
)
LABEL = 'Покупатель — подтверждение заявки v2'


def send_receipt(req):
    from marketing.models import MarketingWhatsAppTemplate
    from core.phone_utils import normalize_phone_for_whatsapp
    # No calls to Meta until the exact approved text has an approved template.
    template = MarketingWhatsAppTemplate.objects.filter(meta_template_name=TEMPLATE_NAME,
        language_code='ru', meta_status='approved', is_active=True, body_text=BODY).first()
    if not template:
        return {'ok': False, 'error_code': 'buyer_receipt_template_not_approved'}
    phone = normalize_phone_for_whatsapp(req.phone)
    if not phone:
        return {'ok': False, 'error_code': 'buyer_phone_missing'}
    with transaction.atomic():
        Request.objects.select_for_update().get(pk=req.pk)
        if WhatsAppMessageLog.objects.filter(request_id=req.pk, seller_name=LABEL).exists():
            return {'ok': False, 'error_code': 'buyer_receipt_already_attempted'}
        log = WhatsAppMessageLog.objects.create(request_id=req.pk, seller_name=LABEL,
            phone_clean=phone, is_success=False, status_text='pending')
    url = 'https://zpt.kz' + reverse('workflow_buyer_step', args=[req.pk, req.access_token, 'sellers'])
    try:
        result = send_whatsapp_template_message(phone, template_name=TEMPLATE_NAME,
            template_language='ru', body_parameters=[wa_template_param(req.pk), wa_template_param(url)],
            buyer_receipt=True)
    except Exception:
        log.status_text = 'send_error'
        log.save(update_fields=['status_text'])
        raise
    log.is_success = bool(result.get('ok'))
    log.status_text = 'sent' if log.is_success else str(result.get('error_code') or 'send_error')[:50]
    log.message_id = result.get('message_id') or ''
    log.error_text = str(result.get('error') or '')[:2000]
    log.save(update_fields=['is_success', 'status_text', 'message_id', 'error_text'])
    return result
