"""One exact new-source application and four bounded display-note corrections."""
import hashlib
import json
from pathlib import Path

MANIFEST_SHA256 = '217bac97a4d55ae8dcd3857ae05cd2de756c2d7e567e2f5054044ecc7d05d608'


def payload():
    raw = (Path(__file__).parent / 'data/reviewed_new12c_fitment.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise ValueError('Reviewed fitment manifest changed')
    data = json.loads(raw)
    if data['checked_on'] != '2026-11-12' or [r['id'] for r in data['offers']] != [35166]:
        raise ValueError('Unexpected reviewed fitment scope')
    return data


def apply_review(apps, schema_editor):
    if schema_editor.connection.alias != 'default':
        return
    from django.db import transaction
    from procurement.analysis import stock_status
    Offer = apps.get_model('procurement', 'Offer')
    Supplier = apps.get_model('procurement', 'Supplier')
    Fitment = apps.get_model('procurement', 'VehicleFitment')
    Activity = apps.get_model('procurement', 'Activity')
    data = payload()
    counts = {'fitment_offers': 0, 'make_model_pairs': 0, 'display_notes': 0}
    with transaction.atomic():
        if Supplier.objects.filter(pk=3, name='Фаетон').exists():
            for row in data['offers']:
                offer = Offer.objects.select_for_update().filter(pk=row['id']).first()
                if (not offer or offer.supplier_id != 3 or offer.brand != row['brand']
                        or offer.article != row['article'] or not offer.match_verified
                        or offer.cost != row['cost'] or offer.kaspi_price != row['kaspi_price']
                        or str(offer.stock) != row['stock'] or stock_status(offer.stock) != 'in_stock'
                        or Fitment.objects.filter(offer_id=offer.pk).exists()):
                    continue
                for fit in row['fitments']:
                    config = {k: v for k, v in fit.items() if k not in ('make', 'model')}
                    config['source_url'] = row['source_url']
                    Fitment.objects.create(offer_id=offer.pk, make=fit['make'], model=fit['model'],
                        configurations=[config], source_url=row['source_url'], verified=True)
                    counts['make_model_pairs'] += 1
                meta = dict(offer.characteristics or {})
                meta['reviewed_applicability'] = {'source_label': row['source_label'],
                    'source_kind': row['source_kind'], 'checked_on': data['checked_on'],
                    'source_url': row['source_url'], 'status': 'sourced',
                    'source_article': row['article'], 'source_brand': row['brand'], 'note': row['note']}
                offer.characteristics = meta
                offer.save(update_fields=['characteristics'])
                counts['fitment_offers'] += 1
        for repair in data['display_note_repairs']:
            offer = Offer.objects.select_for_update().filter(pk=repair['offer_id'], supplier_id=5,
                brand=repair['brand'], article=repair['article']).first()
            if not offer:
                continue
            meta = dict(offer.characteristics or {})
            old = meta.get('reviewed_applicability', {})
            if (old.get('source_url') != repair['source_url'] or old.get('checked_on') != data['checked_on']
                    or old.get('note') != repair['expected_note']):
                continue
            meta['reviewed_applicability'] = {**old, 'note': repair['note']}
            offer.characteristics = meta
            offer.save(update_fields=['characteristics'])
            counts['display_notes'] += 1
        Activity.objects.create(user_id=None, action='reviewed_new12c_import', details=counts)
