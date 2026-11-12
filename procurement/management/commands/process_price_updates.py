from datetime import timedelta
from django.utils import timezone
import logging
import signal
import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from procurement.models import PriceUpdate, KaspiReviewTask
from procurement.kaspi_refresh import refresh_task
from procurement.price_updates import advance_update

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Continue authorized supplier uploads, optionally as a background consumer.'

    def add_arguments(self, parser):
        parser.add_argument('--batch', type=int)
        parser.add_argument('--watch', action='store_true')

    def handle(self, *args, **options):
        running = True
        def stop(*_):
            nonlocal running
            running = False
        if options['watch']:
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
        while running:
            close_old_connections()
            photo_progress = 0
            facet_progress = False
            try:
                query = PriceUpdate.objects.filter(status__in=['ready', 'running']).order_by('pk')
                if options['batch']:
                    query = query.filter(pk=options['batch'])
                for batch in query.iterator(chunk_size=1):
                    rows = batch.rows
                    while running and batch.status != 'done':
                        batch = advance_update(batch.pk, prepared_rows=rows)
                    self.stdout.write(f'Price update {batch.pk}: {batch.cursor}/{batch.row_count}')
                from procurement.models import RetailPublicationRun
                from procurement.stock_retail import advance_publication, start_publication
                # Continue an already-authorized run that previously deferred OEM variants.
                last_retail_run = RetailPublicationRun.objects.select_related('user').order_by('-pk').first()
                if last_retail_run and last_retail_run.status == 'done' and last_retail_run.summary.get('protected', 0):
                    start_publication(last_retail_run.user, missing_only=True)
                for retail_run in RetailPublicationRun.objects.filter(status='running').order_by('pk'):
                    while running and retail_run.status == 'running':
                        retail_run = advance_publication(retail_run.pk)
                    self.stdout.write(f'Retail publication {retail_run.pk}: {retail_run.processed}/{retail_run.total}')
                if options['watch'] and running:
                    from catalog.storefront import refresh_home_facets, refresh_country_facets
                    if refresh_home_facets():
                        self.stdout.write('Public storefront filter snapshot refreshed')
                    facet_progress=refresh_country_facets()
                    from core.seo_views import refresh_sitemap_snapshots
                    if refresh_sitemap_snapshots():
                        self.stdout.write('Public sitemap snapshots refreshed')
                    from catalog.source_photos import process_photo_tasks
                    photo_progress = process_photo_tasks(limit=10)
                if options['watch']:
                    KaspiReviewTask.objects.filter(status='checking', updated_at__lt=timezone.now()-timedelta(minutes=30)).update(status='pending')
                    tasks = KaspiReviewTask.objects.filter(status='pending', offer__match_verified=True,
                        batch__status='done').exclude(offer__kaspi_url='').order_by('priority', 'pk')
                    for task_id in list(tasks.values_list('pk', flat=True)[:10]):
                        if not running:
                            break
                        if not refresh_task(task_id):
                            break  # No immediate retries after an access or source failure.
                        time.sleep(1)
            except Exception:
                if not options['watch']:
                    raise
                logger.exception('Price update paused; committed progress is retained')
            # Polling delays are for an idle queue. Recheck price jobs and other
            # work between photo batches, then keep draining staged photos.
            if options['watch'] and (photo_progress or facet_progress):
                continue
            if not options['watch']:
                break
            for _ in range(10):
                if not running:
                    break
                time.sleep(1)
