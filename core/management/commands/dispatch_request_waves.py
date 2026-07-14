from django.core.management.base import BaseCommand

from core.request_dispatch_service import process_due_dispatch_waves


class Command(BaseCommand):
    help = 'Отправка волн заявок продавцам (не более одной волны на заявку за запуск)'

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

        process_due_dispatch_waves(writer=writer)
