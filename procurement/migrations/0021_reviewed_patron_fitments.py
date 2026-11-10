"""Apply the six already reviewed PATRON rows through the normal safe importer."""
import io
import json
from datetime import date
from pathlib import Path

from django.db import migrations


TARGETS = {'PE40201': 26257, 'PBD4287': 26474, 'PBD1023': 26476,
           'PBD6024': 26479, 'PBD1433': 26512, 'PBD6008': 26513}
CHECKPOINT = 'fitment_source_review_20261111_patron6_pending.json'


def load_reviewed_fitments(apps, schema_editor):
    # This is the same owner-authorized import as the office form, not a worker.
    # Empty/new installations and different suppliers must remain untouched.
    if schema_editor.connection.alias != 'default':
        return
    from django.contrib.auth import get_user_model
    from procurement.models import Offer, Supplier
    from procurement.catalogue_import import import_catalogue_fitments

    supplier = Supplier.objects.filter(pk=5, name='ZakazAuto — Костанай').first()
    user = get_user_model().objects.filter(pk=1, username='admin',
        is_active=True, is_staff=True, is_superuser=True).first()
    if not supplier or not user:
        return
    checkpoint = json.loads((Path(__file__).parent.parent / 'data' / CHECKPOINT).read_text())
    payload = checkpoint['payload']
    entries = payload['offers']
    if payload.get('checked_on') != '2026-11-11' or len(entries) != len(TARGETS):
        raise ValueError('Reviewed PATRON batch changed')
    if {row['article'] for row in entries} != set(TARGETS):
        raise ValueError('Reviewed PATRON identities changed')
    selected = []
    for row in entries:
        if (row.get('brand') != 'PATRON' or row.get('source_brand') != 'PATRON'
                or row.get('source_article') != row['article'] or row.get('status') != 'sourced'):
            raise ValueError('Unreviewed PATRON identity')
        exact = Offer.objects.filter(supplier=supplier, brand='PATRON', article=row['article'])
        offer = exact.filter(pk=TARGETS[row['article']]).first()
        if not offer or exact.count() != 1 or offer.fitments.exists():
            continue
        selected.append(row)
    if selected:
        # Atomic importer locks and rechecks targets, skips existing fitments,
        # validates manufacturer URLs, and updates only fitments/characteristics.
        import_catalogue_fitments(io.StringIO(json.dumps({
            'checked_on': payload['checked_on'], 'offers': selected})),
            supplier.name, date(2026, 11, 11), user)


class Migration(migrations.Migration):
    dependencies = [('procurement', '0020_vehiclefitment_source_url')]
    operations = [migrations.RunPython(load_reviewed_fitments, migrations.RunPython.noop)]
