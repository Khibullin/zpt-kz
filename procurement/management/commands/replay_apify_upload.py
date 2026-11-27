"""Replay retained JSON batches without launching an external search."""
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from procurement.apify_batch_intake import replay_apify_session, process_next_apify_batch
from procurement.models import Activity


class Command(BaseCommand):
    help = 'Переобработать сохранённую загрузку Apify без платного запуска.'

    def add_arguments(self, parser):
        parser.add_argument('session_id', type=int)

    def handle(self, *args, **options):
        try:
            original = Activity.objects.select_related('user').get(
                pk=options['session_id'], action='apify_upload_session')
            if original.user is None:
                raise CommandError('У сессии отсутствует пользователь для расчёта комиссии.')
            session = replay_apify_session(original.pk, original.user)
            while True:
                result = process_next_apify_batch(session.pk, original.user)
                if result['done']:
                    break
            self.stdout.write(self.style.SUCCESS(f'Сессия {session.pk}: {result["stats"]}'))
        except (Activity.DoesNotExist, ValueError, ValidationError) as exc:
            raise CommandError(str(exc)) from exc
