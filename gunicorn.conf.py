"""One durable supplier import consumer per web master, sharing the existing instance.

Only user-staged imports are consumed. No timer initiates supplier downloads,
Kaspi searches, purchases or messages. Database locks serialize multiple masters.
"""
import os
import subprocess
import sys


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
