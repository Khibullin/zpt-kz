"""Approved catalogue import, 17 October 2026. Additive; no dispatch changes.

Payload retains source provenance. Existing values win over differing exports.
New organisations are catalogue-only and cannot receive requests. Migration
rollback never deletes business data. No network calls, signals or messages.
"""
import hashlib
import json
from pathlib import Path
from django.db import migrations
from django.utils.dateparse import parse_datetime

PAYLOAD = Path(__file__).resolve().parents[1] / 'import_data' / 'almaty_parts_20261017.json'
PAYLOAD_SHA = '2c225e8ba65c2a697fd4f729b597894ca11ee00fb4c0d453946b545189b74b44'
PROTECTED = ('whatsapp', 'name', 'city', 'is_active', 'is_paused', 'receive_requests',
             'catalog_only', 'is_dismantler', 'sells_new_parts', 'transport_type',
             'all_brands', 'all_categories', 'all_countries', 'all_models', 'notes')


def apply_batch(apps, payload):
    Seller = apps.get_model('core', 'Seller')
    Card = apps.get_model('core', 'SellerExternalCard')
    Phone = apps.get_model('core', 'SellerExtraPhone')
    Social = apps.get_model('core', 'SellerSocialLink')
    ServiceSeller = apps.get_model('service_requests', 'ServiceSeller')
    counts = {'created': 0, 'updated': 0, 'services_updated': 0}
    for plan in payload['plans']:
        org = plan['org']; expected = plan['seller']
        card = Card.objects.filter(source=org['source'], external_id=org['external_id']).first()
        if expected:
            seller = Seller.objects.select_for_update().get(pk=expected['id'])
            if any(getattr(seller, key) != value for key, value in expected.items()):
                raise RuntimeError('Seller identity changed; re-review row %s' % plan['row'])
            if card and card.seller_id != seller.pk:
                raise RuntimeError('Source belongs to another seller; row %s' % plan['row'])
        elif card:
            seller = Seller.objects.select_for_update().get(pk=card.seller_id)
            if seller.notes != 'Catalogue import ALMATY_PARTS_20261017; source: ' + org['card_url']:
                raise RuntimeError('New source already exists; re-review row %s' % plan['row'])
        else:
            if Seller.objects.filter(name=org['name'], city=org['city'], address=org['address']).exists():
                raise RuntimeError('New matching organisation appeared; re-review row %s' % plan['row'])
            wa = next((x['url'].split('/')[-1] for x in org['socials'] if x['kind'] == 'whatsapp'), '')
            seller = Seller.objects.create(
                name=org['name'], city=org['city'], whatsapp=wa, transport_type='',
                is_active=True, is_paused=False, is_test_seller=False,
                receive_requests=False, catalog_only=True, sells_new_parts=None,
                notes='Catalogue import ALMATY_PARTS_20261017; source: ' + org['card_url'],
            )
            counts['created'] += 1
        before = {key: getattr(seller, key) for key in PROTECTED}
        fills = {}
        for field in ('address', 'website'):
            if org[field] and not getattr(seller, field): fills[field] = org[field]
        if seller.latitude is None and seller.longitude is None:
            fills.update(latitude=org['latitude'], longitude=org['longitude'])
        if not seller.directory_description:
            fills['directory_description'] = plan['categories']
        if plan['directory_parts'] and not seller.directory_parts:
            fills['directory_parts'] = True
        if fills: Seller.objects.filter(pk=seller.pk).update(**fills)
        known = {seller.whatsapp, seller.phone2, *Phone.objects.filter(seller_id=seller.pk).values_list('phone', flat=True)}
        changed = bool(fills)
        for phone in org['phones']:
            if phone in known: continue
            if not seller.phone2:
                Seller.objects.filter(pk=seller.pk).update(phone2=phone); seller.phone2 = phone
            else:
                Phone.objects.get_or_create(seller_id=seller.pk, phone=phone)
            changed = True; known.add(phone)
        for social in org['socials']:
            _, added = Social.objects.get_or_create(seller_id=seller.pk, **social)
            changed |= added
        if card is None:
            Card.objects.create(seller_id=seller.pk, source=org['source'], external_id=org['external_id'], card_url=org['card_url'], exported_at=parse_datetime(org['exported_at']))
            changed = True
        seller.refresh_from_db()
        if before != {key: getattr(seller, key) for key in PROTECTED}:
            raise RuntimeError('Protected fields changed')
        if expected and changed: counts['updated'] += 1
        service_expected = plan.get('service')
        if service_expected:
            service = ServiceSeller.objects.select_for_update().get(pk=service_expected['id'])
            if any(getattr(service, key) != value for key, value in service_expected.items()):
                raise RuntimeError('Service identity changed')
            values = {}
            for field in ('address', 'website'):
                if not getattr(service, field) and org[field]: values[field] = org[field]
            if not service.instagram:
                url = next((x['url'] for x in org['socials'] if x['kind'] == 'instagram'), '')
                if url: values['instagram'] = url
            if not service.description: values['description'] = plan['categories']
            # Replace malformed legacy map text only with the same exact source ID.
            if not service.map_link or (org['external_id'] in service.map_link and not service.map_link.startswith(('http://','https://'))):
                values['map_link'] = org['card_url']
            if values:
                ServiceSeller.objects.filter(pk=service.pk).update(**values)
                counts['services_updated'] += 1
    return counts


def load(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != 'postgresql' or connection.settings_dict.get('NAME') != 'zpt_kz':
        return
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [202610170095])
        cursor.execute("SELECT 1 FROM django_migrations WHERE app='core' AND name='0095_import_almaty_parts_20261017'")
        if cursor.fetchone(): return
    raw = PAYLOAD.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PAYLOAD_SHA:
        raise RuntimeError('Import payload integrity check failed')
    print('ALMATY_PARTS_20261017', apply_batch(apps, json.loads(raw)))


class Migration(migrations.Migration):
    dependencies = [('core', '0094_seller_directory_parts'), ('service_requests', '0011_serviceseller_catalog_notes')]
    operations = [migrations.RunPython(load, migrations.RunPython.noop)]
