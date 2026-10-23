from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.db import transaction
from django.urls import reverse
from core.models import Request, RequestBuyerChannel, RequestBuyerFollowup
from core.services.request_connections import buyer_access_valid, enabled
from core.services.telegram_channel import _call_api, TelegramChannelError


class Command(BaseCommand):
    help = 'План одного вопроса покупателю о результате; отправка Telegram только с --apply и --actor.'
    def add_arguments(self, parser):
        parser.add_argument('--request', type=int, required=True)
        parser.add_argument('--actor', type=int)
        parser.add_argument('--apply', action='store_true')
    def handle(self, *args, **options):
        req = Request.objects.get(pk=options['request'])
        conn = RequestBuyerChannel.objects.filter(request=req, telegram_consent=True, telegram_id__isnull=False).first()
        if not enabled() or not buyer_access_valid(req) or not conn or req.purchase_result in ('purchased', 'closed'):
            raise CommandError('Нет доступной заявки или подтверждённого Telegram покупателя.')
        if not options['apply']:
            self.stdout.write('План одного вопроса покупателю. Сообщения не отправлялись.')
            return
        actor = get_user_model().objects.filter(pk=options['actor'], is_staff=True, is_active=True).first()
        if not actor or not actor.has_perm('core.add_requestbuyerfollowup'):
            raise CommandError('Нужен сотрудник с правом отправки вопроса.')
        with transaction.atomic():
            req = Request.objects.select_for_update().get(pk=req.pk)
            notice, _ = RequestBuyerFollowup.objects.get_or_create(request=req, defaults={'channel':'telegram'})
            if notice.status != 'prepared':
                raise CommandError('Вопрос уже отправлялся или результат отправки неизвестен.')
            notice.status, notice.actor = 'sending', actor
            notice.save()
        try:
            conn.refresh_from_db(); req.refresh_from_db()
            if not conn.telegram_consent or not conn.telegram_id or not buyer_access_valid(req) or req.purchase_result in ('purchased','closed'):
                raise ValueError()
            result = _call_api('sendMessage', {'chat_id':conn.telegram_id, 'text':'Удалось купить запчасть?\nПо заявке №%s\nСообщите, купили ли вы запчасть или ещё продолжаете поиск.' % req.pk,
                'reply_markup': {'inline_keyboard': [[{'text':'Сообщить результат','url':'https://zpt.kz'+reverse('workflow_buyer_step',args=[req.pk,req.access_token,'result'])}]]}})
            message_id=result.get('message_id')
            if type(message_id) is not int or message_id <= 0:
                raise ValueError()
        except (TelegramChannelError, ValueError):
            RequestBuyerFollowup.objects.filter(pk=notice.pk).update(status='unknown')
            raise CommandError('Результат неизвестен. Повторная отправка заблокирована.') from None
        RequestBuyerFollowup.objects.filter(pk=notice.pk).update(status='sent', message_id=message_id)
        self.stdout.write('Telegram принял вопрос. Покупка этим не подтверждается.')
