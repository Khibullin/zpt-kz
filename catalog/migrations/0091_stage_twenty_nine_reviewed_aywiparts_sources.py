"""Stage only twenty-nine individually reviewed AYWIPARTS sources."""
from django.db import migrations

SOURCES = [[21746,23972,44470,3,'AW1320258LR',741],[4401,6428,24720,3,'AW1810407',13646],[4418,6445,24737,3,'AW1810581',13994],[4431,6458,24750,3,'AW1910010W',5677],[4432,6459,24751,3,'AW1910011',5679],[4433,6460,24752,3,'AW1910013',5683],[4434,6461,24754,3,'AW1910017',5691],[4435,6462,24755,3,'AW1910021W',5699],[4436,6463,24757,3,'AW1910024',5705],[4437,6464,24758,3,'AW1910028W',5713],[4438,6465,24759,3,'AW1910029Y',5715],[4439,6466,24760,3,'AW1910031W',5719],[4440,6467,24761,3,'AW1910032W',5721],[4441,6468,24762,3,'AW1910036Y',5729],[4442,6469,24763,3,'AW1910040',5737],[4444,6471,24765,3,'AW1920005',5775],[4445,6472,24766,3,'AW1920007',5779],[4446,6473,24767,3,'AW1920047P',14186],[4449,6476,24770,3,'AW1930025B2',21277],[4452,6479,24773,3,'AW2040038',5965],[4454,6481,24775,3,'AW2055353',5995],[4470,6497,24792,3,'AW2130504',14987],[4474,6501,24796,3,'AW2130699',15127],[4483,6510,24805,3,'AW2220481',23120],[8057,10084,29079,5,'AW1310177L',143],[9820,11847,31000,5,'AW1360838L',10486],[10806,12833,32068,5,'AW1370214LR',2101],[11744,13771,33018,5,'AW1520645',5239],[4459,6486,30565,5,'AW2120354',7034]]


def stage(apps, schema_editor):
    Task = apps.get_model('catalog', 'SupplierPhotoTask')
    # Full task/offer/product/article identity guards; no source discovery.
    for task_id, product_id, offer_id, supplier_id, article, page_id in SOURCES:
        Task.objects.filter(
            pk=task_id, product_id=product_id, offer_id=offer_id,
            status='needs_source', product__status='active',
            product__main_image='', product__article=article,
            offer__supplier_id=supplier_id, offer__brand='AYWIPARTS',
            offer__article=article,
        ).update(
            source_url=f'https://aywiparts.ru/product/view/{page_id}',
            status='maker_ready', note='',
        )


class Migration(migrations.Migration):
    dependencies = [('catalog', '0090_resolve_nineteen_reviewed_aywiparts_sources')]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
