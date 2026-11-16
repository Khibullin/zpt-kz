"""Run the existing photo queue only on an explicit owner/operator command.

No cron, request handler, app startup or background loop invokes this command.
This does not fetch new candidate evidence or change price, stock, or Kaspi.
"""
from django.core.management.base import BaseCommand, CommandError
from catalog.models import SupplierPhotoTask
from catalog.source_photos import process_photo_tasks


class Command(BaseCommand):
    help = 'Manually process up to 100 previously reviewed supplier photo tasks.'

    def add_arguments(self, parser):
        parser.add_argument('--run', action='store_true',
                            help='Required to actually write validated photo results.')
        parser.add_argument('--limit', type=int, default=10,
                            help='Maximum tasks this manual invocation handles (1..100).')

    def handle(self, *args, **options):
        limit = options['limit']
        if not 1 <= limit <= 100:
            raise CommandError('--limit must be between 1 and 100.')
        pending = SupplierPhotoTask.objects.filter(status__in=[
            'pending', 'prepared', 'maker_ready', 'web_ready',
        ]).count()
        if not options['run']:
            self.stdout.write(
                f'DRY RUN: pending/prepared general photo tasks={pending}. '
                f'Use --run --limit {limit} for an explicit manual pass.'
            )
            return
        processed = process_photo_tasks(limit=limit)
        self.stdout.write(
            self.style.SUCCESS(
                f'Manual photo pass completed: processed={processed}, limit={limit}. '
                'Review SupplierPhotoTask status/evidence for exact per-item results.'
            )
        )
