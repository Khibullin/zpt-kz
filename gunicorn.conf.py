"""One durable supplier import consumer per web master, sharing the existing instance.

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
    if os.environ.get('PRICE_UPDATE_WORKER_ENABLED', 'true').lower() == 'true':
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
