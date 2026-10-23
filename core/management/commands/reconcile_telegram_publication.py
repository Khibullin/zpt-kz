"""Manual reconciliation of an uncertain send; never republishes."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from core.models import TelegramRequestPublication, TelegramPublicationAttempt


class Command(BaseCommand):
    help = 'Record an observed Telegram message after manual channel inspection. Never sends.'

    def add_arguments(self, parser):
        parser.add_argument('--publication', type=int, required=True)
        parser.add_argument('--message-id', type=int, required=True)
        parser.add_argument('--actor', type=int)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        if options['message_id'] <= 0:
            raise CommandError('message-id должен быть положительным.')
        pub = TelegramRequestPublication.objects.filter(pk=options['publication']).first()
        if pub is None:
            raise CommandError('Публикация не найдена.')
        if not options['apply']:
            self.stdout.write('План: записать вручную сверенный message_id. Отправки не выполняются.')
            return
        actor = get_user_model().objects.filter(pk=options['actor'], is_active=True, is_staff=True).first()
        if actor is None:
            raise CommandError('Укажите --actor: ID активного сотрудника, выполнившего ручную сверку.')
        if not actor.has_perm('core.change_telegramrequestpublication'):
            raise CommandError('Недостаточно прав на публикацию.')
        with transaction.atomic():
            pub = TelegramRequestPublication.objects.select_for_update().get(pk=pub.pk)
            if pub.message_id and pub.message_id != options['message_id']:
                raise CommandError('Другой message_id уже записан; изменение запрещено.')
            if pub.status == 'published':
                self.stdout.write('Связь уже сохранена.')
                return
            if pub.status not in ('unknown', 'sending'):
                raise CommandError('Допускается только неопределённая отправка.')
            pub.message_id = options['message_id']
            pub.status = 'published'
            pub.save(update_fields=['message_id', 'status', 'updated_at'])
            TelegramPublicationAttempt.objects.create(publication=pub, actor=actor, result='manually_reconciled')
        self.stdout.write('Связь сохранена после ручной сверки. Отправки не выполнялись.')
