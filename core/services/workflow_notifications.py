"""One explicit seller notification at a time, no automatic fan-out or retries."""
from django.db import transaction
from django.urls import reverse
from core.models import RequestSellerNotification, SellerChannelConnection
from core.services.request_connections import safe_summary, enabled
from core.services.telegram_channel import _call_api, TelegramChannelError


def eligible(req, seller, channel):
    if not enabled() or not req.seller_feed_enabled or req.access_revoked or not safe_summary(req) or req.purchase_result in ('purchased', 'closed'):
        return False
    conn = SellerChannelConnection.objects.filter(seller=seller).first()
    return bool(conn and seller.is_active and not seller.is_paused and seller.notification_state == 'active'
                and getattr(conn, channel + '_consent', False)
                and (channel != 'telegram' or conn.telegram_id))


def send_one(req, seller, actor):
    if not eligible(req, seller, 'telegram'):
        raise ValueError('Нет допуска, согласия на уведомления или подключения Telegram.')
    with transaction.atomic():
        # Unique constraint plus seller lock covers first creation.
        type(seller).objects.select_for_update().get(pk=seller.pk)
        notice, _ = RequestSellerNotification.objects.get_or_create(request=req, seller=seller, channel='telegram')
        if notice.status != 'prepared':
            raise ValueError('Повторная отправка запрещена; проверьте историю.')
        notice.status, notice.actor = 'sending', actor
        notice.save()
    try:
        conn = SellerChannelConnection.objects.get(seller=seller)
        req.refresh_from_db()
        seller.refresh_from_db()
        if not eligible(req, seller, 'telegram'):
            raise ValueError('Уведомления отключены.')
        result = _call_api('sendMessage', {'chat_id': conn.telegram_id, 'text': 'Новая заявка на запчасти\n' + safe_summary(req),
            'reply_markup': {'inline_keyboard': [[{'text': 'Написать покупателю',
                'url': 'https://zpt.kz' + reverse('connection_detail', args=[req.pk]) + '?source=telegram'}],
                [{'text': 'Настроить уведомления', 'url': 'https://zpt.kz/connect/seller/channels/'}]]}})
        message_id = result.get('message_id')
        if type(message_id) is not int or message_id <= 0:
            raise ValueError('Нет подтверждения Telegram.')
    except (TelegramChannelError, ValueError):
        RequestSellerNotification.objects.filter(pk=notice.pk).update(status='unknown')
        raise ValueError('Результат неизвестен. Повторная отправка заблокирована.') from None
    RequestSellerNotification.objects.filter(pk=notice.pk).update(status='sent', message_id=message_id)
