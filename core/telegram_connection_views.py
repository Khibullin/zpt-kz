"""Private bot binding. Webhook setup is an explicit deployment operation."""
import json
import secrets
from django.conf import settings
from django.db import transaction, IntegrityError
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from core.models import SellerChannelConnection, TelegramSellerLogin, RequestBuyerChannel
from core.services.telegram_channel import _call_api, TelegramChannelError
from core.services.request_connections import enabled


def reply(chat_id, text, markup=None):
    payload = {'chat_id': chat_id, 'text': text}
    if markup:
        payload['reply_markup'] = markup
    return _call_api('sendMessage', payload)


@csrf_exempt
@require_POST
def telegram_connection_webhook(request):
    secret = getattr(settings, 'TELEGRAM_CONNECTION_WEBHOOK_SECRET', '')
    if not enabled() or not secret or not secrets.compare_digest(
            request.headers.get('X-Telegram-Bot-Api-Secret-Token', ''), secret):
        return HttpResponse(status=403)
    if len(request.body) > 65536:
        return HttpResponse(status=413)
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return HttpResponse(status=400)
    if not isinstance(data, dict):
        return HttpResponse(status=400)
    message = data.get('message') or {}
    callback = data.get('callback_query') or {}
    if message:
        chat, sender = message.get('chat', {}), message.get('from', {})
        text = message.get('text', '')
        if chat.get('type') != 'private' or chat.get('id') != sender.get('id') or sender.get('is_bot'):
            return JsonResponse({'ok': True})
        if isinstance(text, str) and text.startswith('/start buyer_'):
            return buyer_bind_prompt(text, sender['id'])
        if isinstance(text, str) and text.startswith('/start login_'):
            return login_prompt(text, sender['id'])
        if not isinstance(text, str) or not text.startswith('/start bind_'):
            return JsonResponse({'ok': True})
        from uuid import UUID
        try:
            token = UUID(text.split('bind_', 1)[1])
        except (ValueError, AttributeError):
            return JsonResponse({'ok': True})
        with transaction.atomic():
            conn = SellerChannelConnection.objects.select_for_update().select_related('seller').filter(
                binding_token=token, binding_expires_at__gt=timezone.now()).first()
            if conn is None or not conn.seller.user_id or not conn.seller.contact_access_verified:
                return JsonResponse({'ok': True})
            if conn.pending_telegram_id not in (None, sender['id']):
                return JsonResponse({'ok': True})
            conn.pending_telegram_id = sender['id']
            conn.save(update_fields=['pending_telegram_id', 'updated_at'])
        try:
            reply(sender['id'], 'Подключить магазин к Telegram?\n' + conn.seller.name + '\n' + conn.seller.city +
                '\nВы начали подключение в кабинете ZPT.KZ. Если это были не вы, нажмите «Отмена».\nУведомления настраиваются отдельно.',
                {'inline_keyboard': [[{'text': 'Подтвердить привязку', 'callback_data': 'bind:' + token.hex}],
                                     [{'text': 'Отмена', 'callback_data': 'cancel:' + token.hex}]]})
        except TelegramChannelError:
            # No binding happened, retrying this prompt cannot send request contacts.
            pass
    elif callback:
        sender = callback.get('from', {})
        chat = (callback.get('message') or {}).get('chat', {})
        if chat.get('type') != 'private' or chat.get('id') != sender.get('id') or sender.get('is_bot'):
            return JsonResponse({'ok': True})
        from uuid import UUID
        try:
            action, raw = callback.get('data', '').split(':', 1)
            token = UUID(raw)
        except (ValueError, AttributeError):
            return JsonResponse({'ok': True})
        if action == 'buyer':
            return buyer_bind_confirm(token, sender['id'])
        if action == 'login':
            return confirm_login(token, sender['id'])
        if action not in ('bind', 'cancel'):
            return JsonResponse({'ok': True})
        try:
            with transaction.atomic():
                conn = SellerChannelConnection.objects.select_for_update().select_related('seller').filter(
                    binding_token=token, pending_telegram_id=sender['id'], binding_expires_at__gt=timezone.now()).first()
                if conn is None or not conn.seller.contact_access_verified or not conn.seller.is_active:
                    return JsonResponse({'ok': True})
                if action == 'bind':
                    conn.telegram_id = sender['id']
                conn.binding_token = conn.binding_expires_at = conn.pending_telegram_id = None
                conn.save()
        except IntegrityError:
            return JsonResponse({'ok': True})
        try:
            reply(sender['id'], 'Telegram подключён. Уведомления включаются по вашему согласию.' if action == 'bind' else 'Привязка отменена.',
                {'inline_keyboard': [[{'text': 'Настроить уведомления', 'url': 'https://zpt.kz/connect/seller/channels/'}],
                                     [{'text': 'Перейти к заявкам', 'url': 'https://zpt.kz/connect/requests/'}]]})
        except TelegramChannelError:
            pass
    return JsonResponse({'ok': True})


def login_prompt(text, telegram_id):
    from uuid import UUID
    try:
        token = UUID(text.split('login_', 1)[1])
    except ValueError:
        return JsonResponse({'ok': True})
    ticket = TelegramSellerLogin.objects.filter(pk=token, expires_at__gt=timezone.now(), consumed_at__isnull=True).first()
    conn = SellerChannelConnection.objects.select_related('seller').filter(telegram_id=telegram_id).first()
    if ticket and conn and conn.seller.contact_access_verified and conn.seller.is_active:
        try:
            reply(telegram_id, 'Подтвердить вход в ZPT.KZ? Магазин: ' + conn.seller.name +
                  '\nПодтверждайте только вход, который начали сами.',
                  {'inline_keyboard': [[{'text': 'Подтвердить вход', 'callback_data': 'login:' + token.hex}]]})
        except TelegramChannelError:
            pass
    return JsonResponse({'ok': True})


def confirm_login(token, telegram_id):
    conn = SellerChannelConnection.objects.select_related('seller').filter(telegram_id=telegram_id).first()
    if not conn or not conn.seller.contact_access_verified or not conn.seller.is_active:
        return JsonResponse({'ok': True})
    with transaction.atomic():
        ticket = TelegramSellerLogin.objects.select_for_update().filter(pk=token, expires_at__gt=timezone.now(), consumed_at__isnull=True, seller__isnull=True).first()
        if ticket:
            ticket.seller = conn.seller
            ticket.save(update_fields=['seller'])
    try:
        reply(telegram_id, 'Вход подтверждён. Вернитесь на страницу ZPT.KZ и нажмите «Продолжить».')
    except TelegramChannelError:
        pass
    return JsonResponse({'ok': True})


def buyer_bind_prompt(text, telegram_id):
    from uuid import UUID
    try:
        token = UUID(text.split('buyer_', 1)[1])
    except ValueError:
        return JsonResponse({'ok': True})
    with transaction.atomic():
        conn = RequestBuyerChannel.objects.select_for_update().select_related('request').filter(binding_token=token, binding_expires_at__gt=timezone.now(), telegram_consent=True).first()
        from core.services.request_connections import buyer_access_valid
        if conn is None or not buyer_access_valid(conn.request) or conn.pending_telegram_id not in (None, telegram_id):
            return JsonResponse({'ok': True})
        conn.pending_telegram_id = telegram_id
        conn.save(update_fields=['pending_telegram_id'])
    try:
        reply(telegram_id, 'Подключить сообщения по вашей заявке №%s? Подтвердите, только если начали подключение на ZPT.KZ.' % conn.request_id,
              {'inline_keyboard': [[{'text': 'Подтвердить подключение', 'callback_data': 'buyer:' + token.hex}]]})
    except TelegramChannelError:
        pass
    return JsonResponse({'ok': True})


def buyer_bind_confirm(token, telegram_id):
    with transaction.atomic():
        conn = RequestBuyerChannel.objects.select_for_update().select_related('request').filter(binding_token=token, binding_expires_at__gt=timezone.now(), pending_telegram_id=telegram_id, telegram_consent=True).first()
        from core.services.request_connections import buyer_access_valid
        if conn is None or not buyer_access_valid(conn.request):
            return JsonResponse({'ok': True})
        conn.telegram_id = telegram_id
        conn.binding_token = conn.binding_expires_at = conn.pending_telegram_id = None
        conn.save()
    try:
        reply(telegram_id, 'Telegram подключён для сообщений по этой заявке.')
    except TelegramChannelError:
        pass
    return JsonResponse({'ok': True})
