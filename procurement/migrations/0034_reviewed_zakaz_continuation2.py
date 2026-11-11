"""Add two exact reviewed applications; preserve all existing and trade fields."""
import json
from pathlib import Path
from django.db import migrations, transaction

TARGETS = {26820: '273012B100', 26154: '373002B910'}

def apply_review(apps, schema_editor):
    if schema_editor.connection.alias != 'default':
        return
    Offer = apps.get_model('procurement', 'Offer')
    Fitment = apps.get_model('procurement', 'VehicleFitment')
    Activity = apps.get_model('procurement', 'Activity')
    payload = json.loads((Path(__file__).parent.parent / 'data' /
        'reviewed_zakaz_continuation2_20261111.json').read_text())
    if payload['checked_on'] != '2026-11-11' or {r['offer_id']: r['article'] for r in payload['offers']} != TARGETS:
        raise ValueError('Unexpected exact review payload')
    for row in payload['offers']:
        with transaction.atomic():
            offer = Offer.objects.select_for_update().filter(pk=row['offer_id'],
                supplier_id=5, brand='HYUNDAI-KIA', article=TARGETS[row['offer_id']]).first()
            if not offer or Fitment.objects.filter(offer_id=offer.pk).exists():
                continue
            for fit in row['fitments']:
                config = {k: v for k, v in fit.items() if k not in ('make', 'model')}
                config['source_url'] = row['source_url']
                Fitment.objects.create(offer_id=offer.pk, make=fit['make'], model=fit['model'],
                    source_url=row['source_url'], configurations=[config], verified=True)
            metadata = dict(offer.characteristics or {})
            metadata['reviewed_applicability'] = {
                'source_label': row['source_label'], 'source_kind': row['source_kind'],
                'source_url': row['source_url'], 'checked_on': payload['checked_on'],
                'source_article': row['article'], 'source_brand': row['brand'],
                'status': 'sourced', 'note': row['note']}
            Offer.objects.filter(pk=offer.pk).update(characteristics=metadata)
            Activity.objects.create(user_id=None, action='reviewed_fitment_import',
                details={'batch': 'zakaz-continuation2-20261111', 'offer_id': offer.pk,
                    'make_model_pairs': len(row['fitments']), 'source_url': row['source_url']})

class Migration(migrations.Migration):
    dependencies = [('procurement', '0033_reviewed_winkod_shared')]
    operations = [migrations.RunPython(apply_review, migrations.RunPython.noop)]
