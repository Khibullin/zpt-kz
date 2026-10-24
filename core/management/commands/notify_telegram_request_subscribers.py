"""Explicit opt-in batch: new requests only; no invitations or public posts."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from core.models import Request, SellerChannelConnection, SellerChannelConsentEvent, RequestSellerNotification
from core.services.workflow_notifications import eligible, send_one


class Command(BaseCommand):
    help = 'План уведомлений Telegram по новым проверенным заявкам; --apply включает отправку согласившимся продавцам.'
    def add_arguments(self, parser):
        parser.add_argument('--since', help='Only requests created after this timezone-aware ISO timestamp.')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--actor', type=int)
        parser.add_argument('--limit', type=int, default=50)
    def handle(self, *args, **options):
        if options['limit'] < 1 or options['limit'] > 100:
            raise CommandError('limit: от 1 до 100.')
        since = None
        if options.get('since'):
            try:
                since = parse_datetime(options['since'])
            except (TypeError, ValueError):
                since = None
            if since is None or timezone.is_naive(since):
                raise CommandError('since: нужна дата ISO с часовым поясом.')
        actor = None
        if options['apply']:
            actor = get_user_model().objects.filter(pk=options['actor'], is_active=True, is_staff=True).first()
            if not actor or not actor.has_perm('core.add_requestsellernotification'):
                raise CommandError('Нужен сотрудник с правом отправки.')
        count = 0
        for conn in SellerChannelConnection.objects.filter(telegram_consent=True, telegram_id__isnull=False).select_related('seller'):
            # Only requests created after the current explicit consent. Never backfill
            # historical requests, including #515, when enabling this command.
            consent = SellerChannelConsentEvent.objects.filter(seller=conn.seller, channel='telegram').order_by('-created_at', '-pk').first()
            if not consent or not consent.granted:
                continue
            requests = Request.objects.filter(seller_feed_enabled=True, access_revoked=False, created_at__gte=max(consent.created_at, since) if since else consent.created_at).exclude(purchase_result__in=['purchased','closed']).order_by('created_at')
            for req in requests.iterator():
                if RequestSellerNotification.objects.filter(request=req, seller=conn.seller, channel='telegram').exists():
                    continue
                if not eligible(req, conn.seller, 'telegram'):
                    continue
                if options['apply']:
                    try:
                        send_one(req, conn.seller, actor)
                    except ValueError:
                        self.stderr.write('Одна отправка не подтверждена; повтор заблокирован.')
                count += 1
                if count >= options['limit']:
                    break
            if count >= options['limit']:
                break
        self.stdout.write(('Обработано: ' if options['apply'] else 'План без отправки: ') + str(count))
