"""Load two visually reviewed MAHLE applications via the existing atomic importer."""
import io
import json
from datetime import date
from pathlib import Path
from django.db import migrations

TARGETS = {'KC46': 35138, 'LX939': 35142}

def apply_review(apps, schema_editor):
    if schema_editor.connection.alias != 'default':
        return
    from django.contrib.auth import get_user_model
    from procurement.models import Supplier, Offer
    from procurement.catalogue_import import import_catalogue_fitments
    supplier = Supplier.objects.filter(pk=3, name='Фаетон').first()
    user = get_user_model().objects.filter(pk=1, username='admin', is_active=True,
        is_staff=True, is_superuser=True).first()
    if not supplier or not user:
        return
    data = json.loads((Path(__file__).parent.parent / 'data' /
        'fitment_source_review_20261111_mahle2.json').read_text())
    payload = data['payload']
    entries = payload['offers']
    if payload.get('checked_on') != '2026-11-11' or len(entries) != 2 or {r['article'] for r in entries} != set(TARGETS):
        raise ValueError('Unexpected reviewed MAHLE batch')
    selected = []
    for row in entries:
        if row.get('brand') != 'MAHLE' or row.get('source_brand') != 'MAHLE' or row.get('source_article') != row['article'] or row.get('status') != 'sourced':
            raise ValueError('Unexpected source identity')
        exact = Offer.objects.filter(supplier=supplier, brand='MAHLE', article=row['article'])
        offer = exact.filter(pk=TARGETS[row['article']]).first()
        if not offer or exact.count() != 1 or offer.fitments.exists():
            continue
        selected.append(row)
    if selected:
        import_catalogue_fitments(io.StringIO(json.dumps({
            'checked_on': payload['checked_on'], 'offers': selected})),
            supplier.name, date(2026, 11, 11), user)

class Migration(migrations.Migration):
    dependencies = [('procurement', '0021_reviewed_patron_fitments')]
    operations = [migrations.RunPython(apply_review, migrations.RunPython.noop)]
