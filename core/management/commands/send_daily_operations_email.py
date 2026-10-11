from django.conf import settings
from django.core.management.base import BaseCommand

from core.services.operations_email_notifications import send_daily_operations_email


class Command(BaseCommand):
    help = 'Отправляет утреннюю сводку по поиску продавцов, заявкам и публикациям Instagram.'

    def handle(self, *args, **options):
        if not getattr(settings, 'SELLER_PIPELINE_EMAIL_ENABLED', False):
            self.stdout.write(self.style.WARNING(
                'Email-уведомления выключены: установите SELLER_PIPELINE_EMAIL_ENABLED=True.'
            ))
            return
        if send_daily_operations_email():
            self.stdout.write(self.style.SUCCESS('Утренняя сводка отправлена.'))
            return
        self.stderr.write(self.style.ERROR('Утренняя сводка не отправлена.'))
