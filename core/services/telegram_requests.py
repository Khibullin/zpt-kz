"""Explicit, admin-reviewed publishing only. No automatic requests sender."""
from django.db import transaction
from django.urls import reverse
from core.models import TelegramRequestPublication, TelegramPublicationAttempt, Request
from core.services.request_connections import enabled, safe_summary, PII
from core.services.telegram_channel import _call_api, verify_channel, TelegramChannelError


def validate_publication(publication):
    req = publication.request
    if not enabled() or not req.public_consent_at or not req.seller_feed_enabled or not safe_summary(req):
        raise ValueError('Нужны проверка заявки и согласие покупателя на публичную публикацию.')
    text = publication.text.strip()
    forbidden = [req.phone, req.vin, str(req.access_token), req.short_token]
    if not publication.reviewed or not text or len(text) > 1500 or PII.search(text):
        raise ValueError('Проверьте обезличенный текст и отметьте ручную проверку.')
    if any(value and value in text for value in forbidden):
        raise ValueError('Текст содержит закрытые данные заявки.')
    if req.purchase_result in ('purchased', 'closed'):
        raise ValueError('Заявка закрыта.')


def publish(publication_id, actor):
    with transaction.atomic():
        publication = TelegramRequestPublication.objects.select_for_update().select_related('request').get(pk=publication_id)
        # Lock Request as well: a concurrent consent withdrawal must serialize.
        publication.request = Request.objects.select_for_update().get(pk=publication.request_id)
        validate_publication(publication)
        if publication.status != 'draft' or publication.message_id:
            raise ValueError('Повторная отправка запрещена. Сначала сверьте существующую публикацию.')
        publication.status = 'sending'
        publication.save(update_fields=['status', 'updated_at'])
        attempt = TelegramPublicationAttempt.objects.create(publication=publication, actor=actor, result='started')
    try:
        verify_channel()
        # Recheck after verification; do not retry sendMessage on unknown outcome.
        publication.refresh_from_db()
        publication.request.refresh_from_db()
        validate_publication(publication)
        result = _call_api('sendMessage', {
            'chat_id': '@zpt_kz_zakaz', 'text': publication.text,
            'link_preview_options': {'is_disabled': True},
            'reply_markup': {'inline_keyboard': [[{
                'text': 'Есть запчасть / Связаться',
                'url': 'https://zpt.kz' + reverse('connection_detail', kwargs={'req_id': publication.request_id}) + '?source=telegram'}]]}})
        message_id = result.get('message_id')
        if type(message_id) is not int or message_id <= 0:
            raise TelegramChannelError('Telegram не подтвердил публикацию.')
    except (TelegramChannelError, ValueError):
        TelegramRequestPublication.objects.filter(pk=publication.pk).update(status='unknown')
        TelegramPublicationAttempt.objects.filter(pk=attempt.pk).update(result='unknown')
        raise ValueError('Результат не подтверждён. Проверьте канал вручную; повторная отправка заблокирована.') from None
    TelegramRequestPublication.objects.filter(pk=publication.pk).update(status='published', message_id=message_id)
    TelegramPublicationAttempt.objects.filter(pk=attempt.pk).update(result='published')
    return message_id
