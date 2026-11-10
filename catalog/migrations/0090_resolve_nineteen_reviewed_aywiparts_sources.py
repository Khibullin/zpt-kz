"""Resolve nineteen explicitly reviewed AYWIPARTS photo sources.

The catalogue exposes real images for three exact lamp variants. The remaining
pages expose placeholders; the existing consumer records these as skipped.
No discovery, alternate hosts, additional consumers, or existing-photo updates.
"""
from django.db import migrations

SOURCES = [[4210,6237,24521,'AW1360222L',9512],[4229,6256,24541,'AW1370044LR',10560],[4255,6282,24569,'AW1411564',21289],[4302,6329,24619,'AW1421669',22361],[4341,6368,24658,'AW1521258',22586],[4342,6369,24659,'AW1521283',22602],[4390,6417,24708,'AW1810309',13450],[4443,6470,24764,'AW1910063Y',5765],[4447,6474,24768,'AW1930014L',21255],[4448,6475,24769,'AW1930016L',21259],[4475,6502,24797,'AW2220193',22878],[4476,6503,24798,'AW2220258',23102],[4477,6504,24799,'AW2220442',22948],[4478,6505,24800,'AW2220446',23114],[4479,6506,24801,'AW2220464',22956],[4480,6507,24802,'AW2220473',23118],[4481,6508,24803,'AW2220478',22970],[4482,6509,24804,'AW2220480',22972],[4484,6511,24806,'AW2220521',22984]]


def stage(apps, schema_editor):
    Task = apps.get_model('catalog', 'SupplierPhotoTask')
    for task_id, product_id, offer_id, article, page_id in SOURCES:
        Task.objects.filter(
            pk=task_id, product_id=product_id, offer_id=offer_id,
            status='needs_source', product__status='active',
            product__main_image='', product__article=article,
            offer__supplier_id=3, offer__brand='AYWIPARTS',
            offer__article=article,
        ).update(
            source_url=f'https://aywiparts.ru/product/view/{page_id}',
            status='maker_ready', note='',
        )


class Migration(migrations.Migration):
    dependencies = [('catalog', '0089_stage_eight_verified_aywiparts_photos')]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
