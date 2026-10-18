"""Add missing Semey map provenance and contacts; preserve seller dispatch settings."""
import hashlib
import importlib
import json
from pathlib import Path

from django.db import migrations

PAYLOAD = Path(__file__).resolve().parents[1] / 'import_data' / 'semey_dismantlers_20261018.json'
PAYLOAD_SHA = '75f2d66a4d94bd0ad540d988d45e8fabbee893ef6af9fe432820973c686c4e3d'


def apply_batch(apps, payload):
    if len(payload['plans']) != 22 or any(
        not plan['seller'] or plan['confirm_dismantler'] for plan in payload['plans']
    ):
        raise RuntimeError('Semey batch must only enrich 22 reviewed existing source records')
    # Reuse the frozen, tested additive import, with all identities fixed in this payload.
    prior = importlib.import_module('core.migrations.0100_import_almaty_dismantlers_206_20261018')
    return prior.apply_batch(apps, payload)


def load(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != 'postgresql' or connection.settings_dict.get('NAME') != 'zpt_kz':
        return
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [202610180101])
        cursor.execute("SELECT 1 FROM django_migrations WHERE app='core' AND name='0101_enrich_semey_dismantlers_20261018'")
        if cursor.fetchone(): return
    raw = PAYLOAD.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PAYLOAD_SHA:
        raise RuntimeError('Semey payload integrity check failed')
    print('SEMEY_DISMANTLERS_20261018', apply_batch(apps, json.loads(raw)))


class Migration(migrations.Migration):
    dependencies = [('core', '0100_import_almaty_dismantlers_206_20261018')]
    operations = [migrations.RunPython(load, migrations.RunPython.noop)]
