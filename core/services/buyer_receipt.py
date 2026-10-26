"""Agreed buyer receipt text for manual WhatsApp delivery only."""
from django.urls import reverse

BODY = (
    'Ваша заявка №{{1}} принята в работу на ZPT.KZ.\n\n'
    'Дополнительно вы можете выбрать до 10 продавцов по вашей заявке и написать им напрямую, '
    'чтобы уточнить наличие и цены. Готовый текст сообщения мы уже подготовили — вы сможете '
    'проверить и дополнить его перед отправкой.\n\n'
    'Выбрать продавцов: {{2}}\n\n'
    'Сохраните это сообщение, чтобы в любое время вернуться к своей заявке.'
)


def receipt_text(req):
    url = 'https://zpt.kz' + reverse('workflow_buyer_step', args=[req.pk, req.access_token, 'sellers'])
    return BODY.replace('{{1}}', str(req.pk)).replace('{{2}}', url)
