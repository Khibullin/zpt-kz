"""Import two reviewed top-50 items and stage their exact public photos."""
import io
import json
from datetime import date
from pathlib import Path
from django.db import migrations, transaction

TARGETS = {'22-119261': (24826, 6530, 4503, 15), '22-240675': (24832, 6536, 4509, 30)}

def apply_review(apps, schema_editor):
    if schema_editor.connection.alias != 'default':
        return
    from django.contrib.auth import get_user_model
    from procurement.models import Supplier, Offer
    from procurement.catalogue_import import import_catalogue_fitments
    Task = apps.get_model('catalog', 'SupplierPhotoTask')
    supplier = Supplier.objects.filter(pk=3, name='Фаетон').first()
    user = get_user_model().objects.filter(pk=1, username='admin', is_active=True,
        is_staff=True, is_superuser=True).first()
    if not supplier or not user:
        return
    payload = json.loads((Path(__file__).parent.parent / 'data' /
        'fitment_source_review_20261111_top50_bilstein.json').read_text())['payload']
    entries = payload['offers']
    if payload.get('checked_on') != '2026-11-11' or len(entries) != 2 or {r['article'] for r in entries} != set(TARGETS):
        raise ValueError('Unexpected reviewed BILSTEIN batch')
    with transaction.atomic():
        selected = []
        for row in entries:
            article = row['article']
            offer_id, product_id, task_id, count = TARGETS[article]
            url = 'https://cart.bilsteinus.com/strut-' + article + '.html'
            if row.get('brand') != 'BILSTEIN' or row.get('source_brand') != 'BILSTEIN' or row.get('source_article') != article or row.get('status') != 'sourced' or row.get('source_url') != url or len(row['rows']) != count:
                raise ValueError('Unexpected source identity or rows')
            exact = Offer.objects.select_for_update().filter(supplier=supplier, brand='BILSTEIN', article=article)
            offer = exact.filter(pk=offer_id).first()
            if not offer or exact.count() != 1:
                continue
            if not offer.fitments.exists():
                selected.append(row)
            # This new marker is ignored by the old consumer during build.
            Task.objects.filter(pk=task_id, product_id=product_id, offer_id=offer_id,
                status='needs_source', product__status='active', product__main_image='',
                product__article=article, offer__supplier_id=3, offer__brand='BILSTEIN',
                offer__article=article).update(source_url=url, status='bilstein_ready', note='')
        if selected:
            import_catalogue_fitments(io.StringIO(json.dumps({
                'checked_on': payload['checked_on'], 'offers': selected})),
                supplier.name, date(2026, 11, 11), user)

class Migration(migrations.Migration):
    dependencies = [('procurement', '0023_reviewed_mahle60_fitments'),
                    ('catalog', '0091_stage_twenty_nine_reviewed_aywiparts_sources')]
    operations = [migrations.RunPython(apply_review, migrations.RunPython.noop)]
