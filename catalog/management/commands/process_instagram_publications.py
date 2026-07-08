from django.core.management.base import BaseCommand

from catalog.instagram_service import process_approved_instagram_publications


class Command(BaseCommand):
    help = (
        'Обрабатывает очередь Instagram-публикаций: approved → publishing → published/failed. '
        'Зависшие publishing старше 5 минут переводит в failed.'
    )

    def handle(self, *args, **options):
        stats = process_approved_instagram_publications()
        self.stdout.write(
            self.style.SUCCESS(
                'Instagram queue processed. '
                f'processed={stats["processed"]}, '
                f'published={stats["published"]}, '
                f'failed={stats["failed"]}, '
                f'stuck_reset={stats["stuck_reset"]}.'
            )
        )
