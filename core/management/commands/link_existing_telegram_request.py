from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from core.models import Request, TelegramRequestPublication, TelegramPublicationAttempt


class Command(BaseCommand):
    help = 'Record the already published request 515 / message 5. Dry run unless --apply. Never sends.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        req = Request.objects.filter(pk=515).first()
        if req is None:
            raise CommandError('Заявка 515 не найдена.')
        if not options['apply']:
            self.stdout.write('План: связать заявку 515 с https://t.me/zpt_kz_zakaz/5. Без отправки и изменения результата покупки.')
            return
        with transaction.atomic():
            Request.objects.select_for_update().get(pk=515)
            publication, created = TelegramRequestPublication.objects.get_or_create(request=req,
                defaults={'text': 'Историческая публикация: https://t.me/zpt_kz_zakaz/5', 'status': 'published', 'message_id': 5})
            if publication.message_id != 5 or publication.status != 'published':
                raise CommandError('У заявки другая публикация; нужна ручная сверка.')
            if created:
                TelegramPublicationAttempt.objects.create(publication=publication, result='historical_link')
        self.stdout.write('Связь сохранена. Отправки не выполнялись.')
