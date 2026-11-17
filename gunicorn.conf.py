"""Optional supplier import consumer; disabled by default on memory-limited web instances.

Only user-staged imports are consumed. No timer initiates supplier downloads,
Kaspi searches, purchases or messages. Database locks serialize multiple masters.
"""
import os
import subprocess
import sys

# Uploading two supplier files can exceed the sync worker's heartbeat timeout
# while Django reads the request body. Keep the heartbeat in the main thread,
# with a second request thread available while the upload is in progress.
worker_class = 'gthread'
threads = 2


def on_starting(server):
    enabled = os.environ.get('PRICE_UPDATE_WORKER_ENABLED', 'false').lower() == 'true'
    server.log.info('ZPT supplier/Kaspi background processor: %s',
                    'enabled' if enabled else 'disabled')
    # Boolean-only diagnostics: no merchant identifiers, API keys or XML URLs.
    from django.conf import settings
    server.log.info(
        'Kaspi merchant identity: alternate_match_confirmed=%s xml_baseline_present=%s',
        bool(getattr(settings, 'KASPI_MERCHANT_ALIAS_CONFIRMED', False)),
        bool(getattr(settings, 'KASPI_BASELINE_PRICE_FEED_URL', '').strip()),
    )
    if enabled:
        server.price_update_worker = subprocess.Popen(
            [sys.executable, 'manage.py', 'process_price_updates', '--watch'])


def on_exit(server):
    worker = getattr(server, 'price_update_worker', None)
    if worker and worker.poll() is None:
        worker.terminate()
        try:
            worker.wait(timeout=15)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()
