"""Stage eight explicitly checked AYWIPARTS photos through the existing consumer."""
from django.db import migrations

SOURCES = [[4222,6249,24533,'AW1360546R',1819],[4338,6365,24655,'AW1520030',5165],[4340,6367,24657,'AW1521215',21193],[4344,6371,24661,'AW1610063',17672],[4346,6373,24663,'AW1810009',12850],[4352,6379,24669,'AW1810027',12886],[4370,6397,24687,'AW1810144',13120],[4376,6403,24693,'AW1810165',13162]]


def stage(apps, schema_editor):
    Task = apps.get_model('catalog', 'SupplierPhotoTask')
    # Strict identity allowlist: never discover or activate unrelated sources.
    for task_id, product_id, offer_id, article, page_id in SOURCES:
        source = f'https://aywiparts.ru/product/view/{page_id}'
        Task.objects.filter(
            pk=task_id, product_id=product_id, offer_id=offer_id,
            status='needs_source', product__status='active',
            product__main_image='', product__article=article,
            offer__supplier_id=3, offer__brand='AYWIPARTS',
            offer__article=article,
            offer__characteristics__manufacturer_applicability__status='sourced',
            offer__characteristics__manufacturer_applicability__source_url=source,
        ).update(source_url=source, status='maker_ready', note='')


class Migration(migrations.Migration):
    dependencies = [('catalog', '0088_stage_new_verified_photo_sources')]
    operations = [migrations.RunPython(stage, migrations.RunPython.noop)]
