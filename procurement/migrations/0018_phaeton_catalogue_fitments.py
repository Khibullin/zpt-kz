"""Sourced applications; supplier prices and Kaspi evidence are untouched."""
import gzip
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from django.db import migrations


def validate_entry(entry):
    normalise = lambda value: re.sub(r'[-\s]', '', value).upper()
    if normalise(entry['article']) != normalise(entry['source_article']):
        raise ValueError('Manufacturer article mismatch')
    url = urlsplit(entry['source_url'])
    expected = {'ASVA': ('asva-trading.com', '/catalog/'),
                'AYWIPARTS': ('aywiparts.ru', '/product/view/'),
                'BILSTEIN': ('cart.bilsteinus.com', '/shock-'),
                'BLUE PRINT': ('partsfinder.bilsteingroup.com', '/en/article/blueprint/'),
                'AKEBONO': ('www.akebono-brake.com', '/product_technology/aftermarket/docs/pf'),
                'NGK': ('www.ngkntk.com', '/fileadmin/dach/catalogue_coilsleads_2021-22.pdf')}
    host, prefix = expected[entry['brand']]
    if url.scheme != 'https' or url.hostname != host or not url.path.startswith(prefix):
        raise ValueError('Unrecognised catalogue source')
    if entry['status'] not in ('sourced', 'text_needs_review'):
        raise ValueError('Unreviewed catalogue entry')
    if entry['status'] == 'text_needs_review' and entry['rows']:
        raise ValueError('Ambiguous source cannot create verified fitment')
    for row in entry['rows']:
        if not row['make'] or len(row['make']) > 100 or not row['model'] or len(row['model']) > 150:
            raise ValueError('Invalid vehicle identity')


def load_fitments(apps, schema_editor):
    Offer = apps.get_model('procurement', 'Offer')
    Fitment = apps.get_model('procurement', 'VehicleFitment')
    alias = schema_editor.connection.alias
    path = Path(__file__).parent.parent / 'data' / 'phaeton_catalogue_fitments_20261109.json.gz'
    payload = json.loads(gzip.decompress(path.read_bytes()))
    entries = {}
    for entry in payload['offers']:
        validate_entry(entry)
        key = (entry['brand'], entry['article'])
        if key in entries:
            raise ValueError('Duplicate manufacturer article')
        entries[key] = entry
    query = Offer.objects.using(alias).filter(supplier__name__in=['Фаетон', 'Фаэтон', 'Phaeton'],
                                             brand__in={key[0] for key in entries})
    for offer in query.iterator(chunk_size=200):
        entry = entries.get((offer.brand, offer.article))
        if not entry:
            continue
        grouped = {}
        for row in entry['rows']:
            config = {k: v for k, v in row.items() if k not in ('make', 'model')}
            config.setdefault('source_url', entry['source_url'])
            group = grouped.setdefault((row['make'], row['model']), [])
            if config not in group:
                group.append(config)
        creates = []
        existing = set(Fitment.objects.using(alias).filter(offer=offer).values_list('make', 'model'))
        for (make, model), configs in grouped.items():
            if (make, model) not in existing:
                creates.append(Fitment(offer=offer, make=make, model=model, configurations=configs,
                                       source_url=entry['source_url'], verified=True))
        Fitment.objects.using(alias).bulk_create(creates, batch_size=200)
        fields = ['characteristics']
        if not offer.applicability:
            offer.applicability = entry.get('raw_applicability') or '\n'.join(
                f'{make} {model}: ' + '; '.join(dict.fromkeys(c.get('years', '') for c in configs))
                for (make, model), configs in grouped.items())
            fields.append('applicability')
        offer.characteristics = {**(offer.characteristics or {}), 'manufacturer_applicability': {
            'manufacturer': entry['manufacturer'], 'checked_on': entry['checked_on'],
            'source_url': entry['source_url'], 'status': entry['status'], 'note': entry['note']}}
        offer.save(using=alias, update_fields=fields)


class Migration(migrations.Migration):
    dependencies = [('procurement', '0017_phaeton_manufacturer_fitments')]
    operations = [migrations.RunPython(load_fitments, migrations.RunPython.noop)]
