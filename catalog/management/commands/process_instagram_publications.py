from django.core.management.base import BaseCommand

from catalog.instagram_service import (
    get_instagram_publication_queue_diagnostics,
    process_queued_instagram_publications,
)
from core.models import InstagramPublication


class Command(BaseCommand):
    help = (
        'Обрабатывает очередь Instagram-публикаций: queued → publishing → published/failed. '
        'Зависшие publishing старше 5 минут переводит в failed.'
    )

    def handle(self, *args, **options):
        diagnostics = get_instagram_publication_queue_diagnostics()
        self.stdout.write(f'InstagramPublication total={diagnostics["total"]}')
        for status_value, label in InstagramPublication.STATUS_CHOICES:
            count = diagnostics['by_status'].get(status_value, 0)
            self.stdout.write(f'  {status_value} ({label}): {count}')
        self.stdout.write('Recent publications:')
        if diagnostics['recent']:
            for row in diagnostics['recent']:
                self.stdout.write(
                    f'  id={row["id"]} status={row["status"]} created_at={row["created_at"]}'
                )
        else:
            self.stdout.write('  (none)')

        stats = process_queued_instagram_publications()
        self.stdout.write(
            self.style.SUCCESS(
                'Instagram queue processed. '
                f'processed={stats["processed"]}, '
                f'published={stats["published"]}, '
                f'failed={stats["failed"]}, '
                f'stuck_reset={stats["stuck_reset"]}.'
            )
        )
