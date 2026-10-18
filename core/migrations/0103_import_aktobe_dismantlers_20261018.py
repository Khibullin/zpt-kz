"""Approved Excel import. Additive, atomic and repeatable; no provider calls."""
import hashlib
import json
import re
from pathlib import Path

from django.db import migrations
from django.utils.dateparse import parse_datetime

PAYLOAD = Path(__file__).resolve().parents[1] / 'import_data' / 'aktobe_dismantlers_20261018.json'
PAYLOAD_SHA = 'e96f6d18fccc66ae48a963f6ac832ec72801b1f7b900e6d5e80fe2c42831c84b'
MARKER = 'AKTOBE_DISMANTLERS_20261018; source: '


def normalize(value):
    value = re.sub(r'\D', '', value or '')
    return '7' + value[1:] if len(value) == 11 and value.startswith('8') else value


def apply_batch(apps, payload):
    Seller = apps.get_model('core', 'Seller')
    Card = apps.get_model('core', 'SellerExternalCard')
    Phone = apps.get_model('core', 'SellerExtraPhone')
    Social = apps.get_model('core', 'SellerSocialLink')
    counts = {'created': 0, 'updated': 0, 'cards_added': 0}
    updated = set()
    owners = {}
    for sid, first, second in Seller.objects.values_list('id', 'whatsapp', 'phone2'):
        for number in (first, second):
            if number: owners.setdefault(normalize(number), set()).add(sid)
    for sid, number in Phone.objects.values_list('seller_id', 'phone'):
        owners.setdefault(normalize(number), set()).add(sid)

    for plan in payload['plans']:
        org, expected = plan['org'], plan['seller']
        card = Card.objects.filter(source='two_gis', external_id=org['external_id']).first()
        if expected:
            seller = Seller.objects.select_for_update().get(pk=expected['id'])
            for key, value in expected.items():
                if getattr(seller, key) != value:
                    raise RuntimeError('Seller identity changed: row %s' % plan['row'])
            if card and card.seller_id != seller.pk:
                raise RuntimeError('Source owner changed')
        elif card:
            seller = Seller.objects.select_for_update().get(pk=card.seller_id)
            if seller.notes != MARKER + org['card_url']:
                raise RuntimeError('New source already registered elsewhere')
        else:
            if any(owners.get(p) for p in org['phones']):
                raise RuntimeError('New phone conflict: row %s' % plan['row'])
            # Generic names at shared dismantler markets are not unique identities.
            generic = {'центр авторазбора', 'автосервис', 'сто', 'магазин автозапчастей', 'авторазбор', 'пункт авторазбора'}
            if org['name'].replace('\u200b', '').strip().casefold() not in generic and Seller.objects.filter(
                name__iexact=org['name'], city=org['city']
            ).exists():
                raise RuntimeError('New name conflict: row %s' % plan['row'])
            for social in org['socials']:
                if Social.objects.filter(kind=social['kind'], url__iexact=social['url']).exists():
                    raise RuntimeError('New social identity conflict')
            seller = Seller.objects.create(
                name=org['name'], city=org['city'], whatsapp=org['whatsapp'],
                transport_type=plan['transport_type'], is_dismantler=True,
                sells_new_parts=None, catalog_only=True, is_active=True,
                is_paused=False, is_test_seller=False,
                receive_requests=bool(org['whatsapp']),
                all_brands=True, all_models=True, all_countries=True, all_categories=True,
                notes=MARKER + org['card_url'],
            )
            counts['created'] += 1
        if any(owners.get(p, set()) - {seller.pk} for p in org['phones']):
            raise RuntimeError('Phone belongs to another organisation')
        fills = {}
        for field in ('address', 'website'):
            if org[field] and not getattr(seller, field): fills[field] = org[field]
        if seller.latitude is None and seller.longitude is None:
            fills.update(latitude=org['latitude'], longitude=org['longitude'])
        if plan['confirm_dismantler'] and not seller.is_dismantler:
            fills['is_dismantler'] = True
        if fills: Seller.objects.filter(pk=seller.pk).update(**fills)
        changed = bool(fills)
        known = {normalize(seller.whatsapp), normalize(seller.phone2),
                 *Phone.objects.filter(seller_id=seller.pk).values_list('phone', flat=True)}
        for number in org['phones']:
            owners.setdefault(number, set()).add(seller.pk)
            if number in known: continue
            if not seller.phone2:
                Seller.objects.filter(pk=seller.pk).update(phone2=number)
                seller.phone2 = number
            else:
                Phone.objects.get_or_create(seller_id=seller.pk, phone=number)
            known.add(number)
            changed = True
        for social in org['socials']:
            _, added = Social.objects.get_or_create(seller_id=seller.pk, **social)
            changed |= added
        if card is None:
            Card.objects.create(seller_id=seller.pk, source='two_gis',
                                external_id=org['external_id'], card_url=org['card_url'],
                                exported_at=parse_datetime(org['exported_at']))
            counts['cards_added'] += 1
            changed = True
        if expected and changed: updated.add(seller.pk)
    counts['updated'] = len(updated)
    return counts


def load(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != 'postgresql' or connection.settings_dict.get('NAME') != 'zpt_kz':
        return
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [202610180103])
        cursor.execute("SELECT 1 FROM django_migrations WHERE app='core' AND name='0103_import_aktobe_dismantlers_20261018'")
        if cursor.fetchone(): return
    raw = PAYLOAD.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PAYLOAD_SHA:
        raise RuntimeError('Import payload integrity check failed')
    print('AKTOBE_DISMANTLERS_20261018', apply_batch(apps, json.loads(raw)))


class Migration(migrations.Migration):
    dependencies = [('core', '0102_import_kokshetau_dismantlers_20261018')]
    operations = [migrations.RunPython(load, migrations.RunPython.noop)]
