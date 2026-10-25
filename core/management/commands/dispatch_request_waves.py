import logging

from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from django.conf import settings

from core.request_dispatch_service import process_due_dispatch_waves
from service_requests.services.whatsapp_dispatch import (
    process_due_service_request_dispatches,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Отправка волн заявок продавцам и очереди заявок исполнителям'

    def handle(self, *args, **options):
        def writer(message, style=None):
            if style == 'SUCCESS':
                self.stdout.write(self.style.SUCCESS(message))
            elif style == 'ERROR':
                self.stdout.write(self.style.ERROR(message))
            elif style == 'WARNING':
                self.stdout.write(self.style.WARNING(message))
            else:
                self.stdout.write(message)

        failures = []
        try:
            from core.services.telegram_auto_publication import process_new_submissions
            process_new_submissions()
        except Exception:
            self.stderr.write('Telegram publication processor failed; inspect publication history.')
            failures.append('Telegram channel')

        try:
            process_due_dispatch_waves(writer=writer)
        except Exception as exc:
            logger.exception('core RequestDispatch processor failed')
            self.stderr.write(self.style.ERROR(
                f'core RequestDispatch processor failed: {exc}'
            ))
            failures.append('core RequestDispatch')

        try:
            process_due_service_request_dispatches(writer=writer)
        except Exception as exc:
            logger.exception(
                'service_requests ServiceRequestDispatch processor failed'
            )
            self.stderr.write(self.style.ERROR(
                'service_requests ServiceRequestDispatch processor failed: '
                f'{exc}'
            ))
            failures.append('service_requests ServiceRequestDispatch')

        if getattr(settings, 'TELEGRAM_SUBSCRIBER_DELIVERY_ENABLED', False):
            try:
                actor = str(getattr(settings, 'TELEGRAM_SUBSCRIBER_DELIVERY_ACTOR_ID', ''))
                since = getattr(settings, 'TELEGRAM_SUBSCRIBER_DELIVERY_SINCE', '')
                if not actor.isdigit() or not since:
                    raise CommandError('Telegram delivery requires actor and start timestamp.')
                call_command('notify_telegram_request_subscribers', apply=True,
                    actor=int(actor), since=since, limit=50, stdout=self.stdout, stderr=self.stderr)
            except Exception:
                # Never include API credentials or request payloads in worker output.
                self.stderr.write('Telegram subscriber processor failed; inspect notification history.')
                failures.append('Telegram subscribers')

        if failures:
            raise CommandError(
                'Dispatch processor failed: ' + ', '.join(failures)
            )
