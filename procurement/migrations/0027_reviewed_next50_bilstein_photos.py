"""Stage two exact, independently reviewed next-50 photos only."""
from django.db import migrations, transaction


def stage(apps, schema_editor):
    if schema_editor.connection.alias != 'default':
        return
    Offer = apps.get_model('procurement', 'Offer')
    Product = apps.get_model('catalog', 'Product')
    Task = apps.get_model('catalog', 'SupplierPhotoTask')
    Listing = apps.get_model('procurement', 'RetailListing')
    targets = [('19-135021', 24816, 6520, 4493), ('19-246390', 24821, 6525, 4498)]
    with transaction.atomic():
        for article, offer_id, product_id, task_id in targets:
            offer = Offer.objects.select_for_update().filter(pk=offer_id, supplier_id=3, brand='BILSTEIN', article=article).first()
            product = Product.objects.select_for_update().filter(pk=product_id, article=article, status='active', main_image='').first()
            if not offer or not product or not Listing.objects.filter(offer_id=offer_id, product_id=product_id, enabled=True).exists():
                continue
            task = Task.objects.select_for_update().filter(pk=task_id, offer_id=offer_id, product_id=product_id, status='needs_source').first()
            if not task:
                continue
            task.source_url = 'https://cart.bilsteinus.com/shock-' + article + '.html'
            task.status = 'bilstein_shock_ready'
            task.note = 'Точная официальная фотография проверена; ожидает действующего загрузчика.'
            task.evidence = {**(task.evidence or {}), 'review_batch': 'phaeton-next50-bilstein-20261111', 'checked_on': '2026-11-11', 'source_probe': True}
            task.save(update_fields=['source_url', 'status', 'note', 'evidence', 'updated_at'])


class Migration(migrations.Migration):
    dependencies = [('procurement', '0026_reviewed_top50_supplemental_photos')]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
