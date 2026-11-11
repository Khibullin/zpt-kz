"""Restage two exact packaged photos after an old cached manifest rejected them."""
from django.db import migrations, transaction

TARGETS = ((2990, 5017, 23292, 'ASVA', '0212-008'),
           (13929, 15956, 35146, 'MAHLE', 'AVE81000P'))


def stage(apps, schema_editor):
    if schema_editor.connection.alias != 'default':
        return
    from catalog.reviewed_top50 import reviewed_rows, MANIFEST_SHA256, BATCH
    rows = {r['offer_id']: r for r in reviewed_rows() if r.get('main_b64')}
    Task = apps.get_model('catalog', 'SupplierPhotoTask')
    Listing = apps.get_model('procurement', 'RetailListing')
    Product = apps.get_model('catalog', 'Product')
    with transaction.atomic():
        for task_id, product_id, offer_id, brand, article in TARGETS:
            row = rows.get(offer_id)
            if not row or (row['brand'], row['article']) != (brand, article):
                continue
            product = Product.objects.select_for_update().filter(pk=product_id,
                status='active', article=article, main_image='').first()
            task = Task.objects.select_for_update().filter(pk=task_id,
                product_id=product_id, offer_id=offer_id, offer__supplier_id=3,
                offer__brand=brand, offer__article=article, status='skipped',
                note='Карточка изменилась; адресное добавление отменено.').first()
            source = row.get('image_source_url', row['source_url'])
            if (not product or not task or task.source_url != source
                    or task.evidence.get('reviewed_manifest_sha256') != MANIFEST_SHA256
                    or not Listing.objects.filter(product_id=product_id,
                        offer_id=offer_id, enabled=True).exists()):
                continue
            # The previous live consumer does not know this marker. Only the
            # deployed consumer with the current, checksum-verified manifest acts.
            task.status = 'top50_v2_ready'
            task.note = 'Повторное локальное сохранение двух проверенных фото после смены версии.'
            task.evidence = {**task.evidence, 'reviewed_batch': BATCH,
                             'recovery': 'two-cached-manifest-rejections-20261111'}
            task.save(update_fields=['status', 'note', 'evidence', 'updated_at'])


class Migration(migrations.Migration):
    dependencies = [('procurement', '0030_reviewed_zakaz100')]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
