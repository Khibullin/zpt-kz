import logging

from django.core.management.base import BaseCommand, CommandError

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

        if failures:
            raise CommandError(
                'Dispatch processor failed: ' + ', '.join(failures)
            )
