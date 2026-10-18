"""Owner-approved future-request activation; no queue or provider calls."""
import hashlib
import json
from pathlib import Path

from django.db import migrations

PAYLOAD = Path(__file__).resolve().parents[1] / 'import_data' / 'activate_all_imports_20261018.json'
PAYLOAD_SHA = 'a797ebfabc2dd7d53c80e4db27dfc242466de93b1bf19f415d24975312878339'


def activate(apps, payload):
    Seller = apps.get_model('core', 'Seller')
    Dispatch = apps.get_model('core', 'RequestDispatch')
    changed = 0
    for plan in payload['plans']:
        seller = Seller.objects.select_for_update().get(pk=plan['id'])
        if any(getattr(seller, key) != plan['before'][key] for key in
               ('name', 'whatsapp', 'city', 'is_test_seller', 'is_dismantler', 'transport_type')):
            raise RuntimeError('Imported seller identity changed: %s' % seller.pk)
        if all(getattr(seller, key) == value for key, value in plan['updates'].items()):
            continue
        if any(getattr(seller, key) != value for key, value in plan['before'].items()):
            raise RuntimeError('Activation state changed: %s' % seller.pk)
        if seller.is_test_seller:
            raise RuntimeError('Test seller cannot be activated')
        if Dispatch.objects.filter(seller_id=seller.pk, status='queued').exists():
            raise RuntimeError('Historical queue requires review: %s' % seller.pk)
        Seller.objects.filter(pk=seller.pk).update(**plan['updates'])
        changed += 1
    return changed


def load(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != 'postgresql' or connection.settings_dict.get('NAME') != 'zpt_kz':
        return
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [202610180114])
        cursor.execute("SELECT 1 FROM django_migrations WHERE app='core' AND name='0114_activate_all_imported_organisations'")
        if cursor.fetchone():
            return
    raw = PAYLOAD.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PAYLOAD_SHA:
        raise RuntimeError('Activation payload integrity check failed')
    print('ACTIVATE_ALL_IMPORTS_20261018 changed:', activate(apps, json.loads(raw)))


class Migration(migrations.Migration):
    dependencies = [('core', '0113_import_semey_parts_sellers_20261018')]
    operations = [migrations.RunPython(load, migrations.RunPython.noop)]
