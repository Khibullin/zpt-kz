"""Stage only the explicitly verified AYWIPARTS photograph."""
from django.db import migrations


def stage(apps, schema_editor):
    Task = apps.get_model('catalog', 'SupplierPhotoTask')
    # The general producer can discover unrelated historical sources. This
    # bounded import must never activate ASVA or other unreviewed domains.
    Task.objects.filter(
        pk=4134, product_id=6161, offer_id=24442,
        status='needs_source', product__status='active',
        product__main_image='', product__article='AW1320356LR',
        offer__brand='AYWIPARTS', offer__article='AW1320356LR',
    ).update(
        source_url='https://aywiparts.ru/product/view/22347',
        status='maker_ready', note='',
    )


class Migration(migrations.Migration):
    dependencies = [('catalog', '0087_stage_public_web_photos')]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
