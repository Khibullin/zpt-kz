from datetime import timedelta
from django.utils import timezone
import logging
import signal
import time
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import close_old_connections, connection
from django.db.models import Q
from procurement.models import PriceUpdate, KaspiReviewTask
from procurement.kaspi_refresh import refresh_task
from procurement.omega_policy import eligible_scheduled_kaspi_tasks
from procurement.price_updates import advance_update

logger = logging.getLogger(__name__)


def photo_window_open(now):
    """Keep one consumer: nights on weekdays, all day on Saturday and Sunday."""
    from zoneinfo import ZoneInfo

    local_now = now.astimezone(ZoneInfo('Asia/Qyzylorda'))
    return local_now.weekday() >= 5 or local_now.hour >= 23 or local_now.hour < 6



def check_kaspi_transport():
    """One bounded DNS/TCP/TLS diagnostic after a failed official GET."""
    import socket
    import ssl
    for host in ('kaspi.kz', 'guide.kaspi.kz'):
        try:
            socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except OSError as error:
            logger.warning('Kaspi transport check: host=%s stage=dns error=%s',
                           host, type(error).__name__)
            continue
        try:
            with socket.create_connection((host, 443), timeout=4) as tcp:
                tcp.settimeout(4)
                with ssl.create_default_context().wrap_socket(
                    tcp, server_hostname=host) as sock:
                    logger.warning('Kaspi transport check: host=%s stage=tls success=%s',
                                   host, bool(sock.version()))
        except OSError as error:
            logger.warning('Kaspi transport check: host=%s stage=tcp_or_tls error=%s',
                           host, type(error).__name__)



def check_kaspi_direct_get():
    """Single direct read-only GET to isolate environment proxy routing issues.

    No proxy rotation, retries, CAPTCHA handling, product publication or keys
    are logged. Test uses the same official documented endpoint and auth.
    """
    import os
    import requests
    configured = any(bool(os.environ.get(name))
                     for name in ('HTTPS_PROXY','https_proxy','HTTP_PROXY','http_proxy'))
    logger.warning('Kaspi transport diagnostic: environment_proxy_present=%s',configured)
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(
            'https://kaspi.kz/shop/api/products/classification/categories',
            headers={'Accept':'application/json',
                     'X-Auth-Token':settings.KASPI_API_TOKEN,
                     'User-Agent':'ZPT.KZ-Kaspi-Integration/1.0'},
            timeout=(3,6), allow_redirects=False,
        )
        logger.warning('Kaspi direct categories GET: http_status=%s',response.status_code)
        return response.status_code
    except requests.Timeout:
        logger.warning('Kaspi direct categories GET: timed_out')
    except requests.RequestException as error:
        logger.warning('Kaspi direct categories GET: network_error=%s',
                       type(error).__name__)
    return None


def verify_catalog_readonly():
    """One safe official Kaspi API GET on worker start, without leaking secrets."""
    from procurement.kaspi_catalog_api import CatalogClient, CatalogError, connected
    token_set = connected()
    merchant_set = bool(getattr(settings, 'KASPI_MERCHANT_ID', '').strip())
    alternate_set = bool(getattr(settings, 'AGPARTS_KASPI_MERCHANT_ID', '').strip())
    baseline_set = bool(getattr(settings, 'KASPI_BASELINE_PRICE_FEED_URL', '').strip())
    feed_point_set = bool(getattr(settings, 'KASPI_POINT_OF_SERVICE', '').strip())
    feed_connected = bool(getattr(settings, 'KASPI_PRICE_FEED_CONNECTED', False))
    logger.warning('Kaspi official API configuration: token_present=%s merchant_present=%s alternate_merchant_present=%s baseline_xml_present=%s point_of_service_present=%s price_feed_connected=%s',
                token_set, merchant_set, alternate_set, baseline_set, feed_point_set, feed_connected)
    if not token_set:
        logger.warning('Kaspi official catalogue GET skipped: token is not configured')
        return False
    try:
        response = CatalogClient().categories()
        if not isinstance(response, list):
            logger.warning('Kaspi official categories GET returned unexpected JSON structure: %s',
                           type(response).__name__)
            return False
        logger.info('Kaspi official categories GET success: category_count=%s', len(response))
        return True
    except CatalogError as exc:
        # CatalogError is sanitized by CatalogClient: no header or token values.
        logger.warning('Kaspi official categories GET failed: %s', str(exc))
        if 'соединение' in str(exc) or 'тайм-аут' in str(exc):
            check_kaspi_transport()
            # One additional DOCUMENTED, read-only API endpoint. Never POST,
            # enumerate products, or retry on authentication failures.
            check_kaspi_direct_get()
            try:
                schema = CatalogClient().call('import/schema')
                logger.warning('Kaspi official import-schema GET: success=%s',
                               isinstance(schema, dict))
            except CatalogError as secondary:
                logger.warning('Kaspi official import-schema GET failed: %s', secondary)
        return False
    except Exception:
        logger.exception('Kaspi official categories GET failed unexpectedly (no secrets logged)')
        return False


class Command(BaseCommand):
    help = 'Continue authorized supplier uploads, optionally as a background consumer.'

    def add_arguments(self, parser):
        parser.add_argument('--batch', type=int)
        parser.add_argument('--watch', action='store_true')
        parser.add_argument('--max-watch-passes', type=int, default=0,
                            help='Gracefully exit after this many complete consumer passes; 0 disables recycling.')

    def handle(self, *args, **options):
        running = True
        def stop(*_):
            nonlocal running
            running = False
        if options['watch']:
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            # Never block the durable price worker on external API diagnostics.
            # Use the one-shot diagnostic command instead (no publishing).
            logger.info('Official Kaspi API health check is separate from the price consumer')
        passes_started = 0
        while running:
            # Exit only at a pass boundary. No in-flight supplier batch or
            # confirmed price is interrupted; Gunicorn restarts the consumer.
            if (options['watch'] and options['max_watch_passes'] > 0
                    and passes_started >= options['max_watch_passes']):
                logger.info('Price consumer recycling after %s passes', passes_started)
                break
            passes_started += 1
            close_old_connections()
            photo_progress = 0
            try:
                # Supplier price uploads have absolute priority: Kaspi access
                # errors, images or storefront snapshots must never delay a
                # freshly uploaded supplier file or its profitable calculation.
                query = PriceUpdate.objects.filter(status__in=['ready', 'running'], supplier__name='Omega').order_by('pk')
                if options['batch']:
                    query = query.filter(pk=options['batch'])
                for batch in query.iterator(chunk_size=1):
                    rows = batch.rows
                    try:
                        while running and batch.status != 'done':
                            batch = advance_update(batch.pk, prepared_rows=rows)
                        self.stdout.write(f'Price update {batch.pk}: {batch.cursor}/{batch.row_count}')
                    finally:
                        # A staged supplier import can contain a very large JSON
                        # array. Release BOTH references before the long-lived
                        # consumer continues with unrelated background jobs.
                        # Otherwise the completed import can stay resident for
                        # hours, causing a 512-MiB Render instance to be OOM killed.
                        del rows
                        del batch
                if options['watch']:
                    from procurement.supplier_price_comparison import _snapshot
                    from decimal import Decimal
                    _snapshot(Decimal('2000'), warm_only=True)
                    from procurement.selection_rule_views import warm_selection_preview
                    warm_selection_preview()
                    # Kaspi validation and image work are independent from uploads.
                    # storefront and purchased-card work, so Kaspi does not
                    # starve behind unrelated enrichment queues.
                    KaspiReviewTask.objects.filter(
                        status='checking',
                        updated_at__lt=timezone.now()-timedelta(minutes=30),
                    ).update(status='pending')
                    from procurement.kaspi_identity_worker import (
                        process_pending_bound_cards,
                        resume_source_blocked_after_recovery,
                    )
                    # At most one daily probe; no repeat request after access
                    # denial. On verified recovery the pending queue resumes.
                    resume_source_blocked_after_recovery()
                    process_pending_bound_cards(limit=1)
                    # Share existing, independently verified collector observations
                    # as INCOMPLETE evidence only, never as confirmed market prices.
                    from django.core.cache import cache
                    if cache.add('procurement:collector-preview-scan', True, 900):
                        from procurement.kaspi_snapshot_bridge import sync_verified_preview_batch
                        sync_verified_preview_batch(limit=10)
                    from procurement.kaspi_card_jobs import process_card_jobs
                    process_card_jobs()
                    from procurement.kaspi_price_feed import refresh_price_feed
                    refresh_price_feed()
                    from procurement.filter_snapshots import refresh_filter_snapshots
                    refresh_filter_snapshots()
                    from procurement.kaspi_materials import process_purchased_cards
                    process_purchased_cards(fetch_photos=True, throttle=True)
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
                if options['watch'] and running and getattr(settings, 'PUBLIC_CATALOG_ENABLED', True):
                    from catalog.storefront import refresh_home_facets, refresh_country_facets, refresh_brand_facets
                    if refresh_home_facets():
                        self.stdout.write('Public storefront filter snapshot refreshed')
                    refresh_country_facets()
                    # Fill public make/model snapshots in bounded batches before
                    # resuming other idle work; price uploads stay first.
                    # One make per pass: on a shared half-CPU instance a busy
                    # refresh loop must leave time for office requests and SQL.
                    if running:
                        refresh_brand_facets()
                    from core.seo_views import refresh_sitemap_snapshots
                    if refresh_sitemap_snapshots():
                        self.stdout.write('Public sitemap snapshots refreshed')
                    from catalog.source_photos import process_photo_tasks
                    # Photo enrichment is owner-triggered only. The price worker
                    # must not drain the photo queue automatically during idle loops.
                    if getattr(settings, 'CATALOG_PHOTO_AUTORUN_ENABLED', False) and photo_window_open(timezone.now()):
                        photo_progress = process_photo_tasks(limit=10)
                if options['watch']:
                    tasks = eligible_scheduled_kaspi_tasks(
                        KaspiReviewTask.objects.filter(
                            status='pending', offer__match_verified=True,
                        ).filter(
                            Q(batch__isnull=True) | Q(batch__status='done')
                        ).exclude(offer__kaspi_url='')
                    ).order_by('priority', 'offer__kaspi_price_checked_on', 'pk')
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
                # A lost PostgreSQL socket can remain attached to this long-lived
                # worker after a transient failure. Start the next pass with a
                # fresh connection; already committed progress is not rolled back.
                connection.close()
            # Polling delays are for an idle queue. Recheck price jobs and other
            # work between photo batches, then keep draining staged photos.
            from core.process_memory import MIB, release_idle_memory
            resident = release_idle_memory()
            if options['watch'] and resident > 192 * MIB:
                logger.info('Price consumer recycling after complete pass: resident_bytes=%s', resident)
                break
            if options['watch'] and photo_progress:
                continue
            if not options['watch']:
                break
            for _ in range(10):
                if not running:
                    break
                time.sleep(1)
