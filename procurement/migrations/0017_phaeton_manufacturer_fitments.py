"""Restore exact 555 article applications from the manufacturer's catalogue."""
import json
import gzip
from pathlib import Path
from urllib.parse import urlsplit

from django.db import migrations


def load_fitments(apps, schema_editor):
    Offer = apps.get_model('procurement', 'Offer')
    Fitment = apps.get_model('procurement', 'VehicleFitment')
    alias = schema_editor.connection.alias
    payload = json.loads(gzip.decompress((Path(__file__).parent.parent / 'data' / 'phaeton_555_fitments_20261109.json.gz').read_bytes()))
    offers = Offer.objects.using(alias).filter(
        supplier__name__in=['Фаетон', 'Фаэтон', 'Phaeton'], brand='555')
    by_article = {entry['article']: entry for entry in payload['offers']}
    for offer in offers.iterator(chunk_size=200):
        entry = by_article.get(offer.article)
        if not entry:
            continue
        grouped = {}
        for row in entry['rows']:
            if row['article'] != offer.article:
                raise ValueError('Manufacturer article mismatch')
            source = urlsplit(row['source_url'])
            if source.scheme != 'https' or source.hostname != 'www.sankei-555.com' or source.path != '/search/detail.php':
                raise ValueError('Unrecognised manufacturer source')
            key = (row['make'], row['model'])
            group = grouped.setdefault(key, {'source_url': row['source_url'], 'configurations': []})
            config = {'years': row['years'], 'chassis': row['chassis'], 'position': row['position'],
                      'source_url': row['source_url']}
            if config not in group['configurations']:
                group['configurations'].append(config)
        summaries = []
        for (make, model), group in grouped.items():
            # Leave an existing reviewed pair intact. A later catalogue snapshot
            # must not silently replace a staff correction or its restrictions.
            if not Fitment.objects.using(alias).filter(offer=offer, make=make, model=model, details='').exists():
                Fitment.objects.using(alias).create(offer=offer, make=make, model=model,
                    source_url=group['source_url'], configurations=group['configurations'], verified=True)
            variants = '; '.join(' / '.join(filter(None, [c['chassis'], c['years'], c['position']]))
                                 for c in group['configurations'])
            summaries.append(f'{make} {model}: {variants}')
        if grouped:
            fields = ['characteristics']
            if not offer.applicability:
                offer.applicability = '\n'.join(summaries)
                fields.append('applicability')
            offer.characteristics = {**(offer.characteristics or {}), 'manufacturer_applicability': {
                'manufacturer': '555 / Sankei', 'checked_on': payload['checked_on'],
                'source_url': entry['rows'][0]['source_url'], 'status': 'sourced',
                'note': 'Годы и коды шасси приведены по каталогу производителя; конец открытого диапазона не указан.'}}
            offer.save(using=alias, update_fields=fields)


class Migration(migrations.Migration):
    dependencies = [('procurement', '0016_price_date_evidence')]
    operations = [migrations.RunPython(load_fitments, migrations.RunPython.noop)]
