"""Optional supplier import consumer; disabled by default on memory-limited web instances.

Only user-staged imports are consumed. No timer initiates supplier downloads,
Kaspi searches, purchases or messages. Database locks serialize multiple masters.
"""
import os
import subprocess
import sys
import time

# Uploading two supplier files can exceed the sync worker's heartbeat timeout
# while Django reads the request body. Keep the heartbeat in the main thread,
# with a second request thread available while the upload is in progress.
worker_class = 'gthread'
threads = 2
# Recycle the request worker before cumulative allocations hit Render's 512 MiB limit.
# Gunicorn drains in-flight requests before replacing a worker.
max_requests = 100
max_requests_jitter = 20


def post_request(worker, req, environ, resp):
    from core.process_memory import MIB, release_idle_memory
    # gthread drains in-flight requests before exiting, just as max_requests
    # does. A large completed response must not keep growing the shared 512 MiB
    # instance until the next request-count recycle.
    resident = release_idle_memory()
    if resident > 192 * MIB and worker.alive:
        worker.log.info('Recycling idle HTTP worker: resident_bytes=%s', resident)
        worker.alive = False


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
        _spawn_price_update_worker(server)


def _spawn_price_update_worker(server):
    # Complete each durable import pass before recycling the long-lived
    # consumer. Gunicorn's master supervises it separately from HTTP workers.
    server.price_update_worker = subprocess.Popen(
        [sys.executable, 'manage.py', 'process_price_updates', '--watch',
         '--max-watch-passes', '120'])
    server.price_update_worker_started = time.monotonic()


def on_tick(server):
    worker = getattr(server, 'price_update_worker', None)
    if worker is None or worker.poll() is None:
        return
    # Back off if the command exits immediately because of a transient
    # database failure; never start overlapping consumers.
    if time.monotonic() - getattr(server, 'price_update_worker_started', 0) < 30:
        return
    server.log.warning('ZPT price consumer exited (code=%s); restarting',
                       worker.returncode)
    _spawn_price_update_worker(server)


def on_exit(server):
    worker = getattr(server, 'price_update_worker', None)
    if worker and worker.poll() is None:
        worker.terminate()
        try:
            worker.wait(timeout=15)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()
