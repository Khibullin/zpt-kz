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
    # Boolean-only diagnostics; avoid importing Django before app startup.
    alt = (os.environ.get('AGPARTS_KASPI_MERCHANT_ID') or '').strip()
    primary = (os.environ.get('KASPI_MERCHANT_ID') or '').strip()
    owned = {value.strip() for value in
             os.environ.get('KASPI_OWN_MERCHANT_IDS', '').split(',')
             if value.strip()}
    alias_confirmed = bool(alt and alt in owned and
                           (not primary or primary == alt))
    server.log.info(
        'Kaspi merchant identity: alternate_match_confirmed=%s xml_baseline_present=%s',
        alias_confirmed,
        bool(os.environ.get('KASPI_BASELINE_PRICE_FEED_URL', '').strip()),
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
