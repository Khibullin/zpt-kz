"""Seed source-separated comparison records for Omega review items 361–460.

Existing detailed materials stay unchanged; no Kaspi product properties,
current prices, identity, or availability are asserted by these records.
"""
from django.db import migrations

SIXTH_IDS = (
    1981, 6692, 17523, 20062, 17670, 123, 3228, 2565, 7442, 19376,
    7247, 752, 15773, 16480, 4235, 4236, 4159, 5033, 5396, 1562,
    454, 15561, 8280, 5145, 5505, 4515, 9926, 1743, 19405, 5191,
    5192, 20645, 20705, 17720, 5364, 17662, 5265, 19373, 16483, 15416,
    7313, 5449, 6696, 15500, 7321, 1442, 45, 13809, 10516, 1304,
    848, 16178, 13853, 4566, 396, 313, 13794, 544, 471, 4922,
    1240, 15216, 15660, 1163, 1977, 13592, 17579, 17580, 19438, 10584,
    15780, 15217, 10, 15253, 5617, 15238, 4174, 15218, 15237, 15275,
    15228, 698, 15262, 15256, 15258, 15260, 15259, 19453, 15257, 15264,
    8672, 14011, 2581, 15265, 5399, 5079, 4240, 4239, 4164, 7445,
)


def prepare_sixth_batch(apps, schema_editor):
    Offer = apps.get_model('procurement', 'Offer')
    Audit = apps.get_model('procurement', 'KaspiPriceAudit')
    Material = apps.get_model('procurement', 'BuyingComparisonMaterial')
    for offer in Offer.objects.filter(pk__in=SIXTH_IDS).iterator(chunk_size=200):
        audit = Audit.objects.filter(offer_id=offer.pk).order_by('-observed_at', '-pk').first()
        if not audit or not audit.product_id or not audit.card_url:
            continue
        photo = str(offer.photo_url or '')
        supplier_data = {
            'name': offer.name or '',
            'brand': offer.brand or '',
            'article': offer.article or '',
            'category': offer.category or '',
            'description': offer.description or '',
            'fitment': offer.applicability or '',
            'images': [photo] if photo.startswith('https://') else [],
            'source_note': 'Сведения из сохранённого каталога Omega; страница поставщика не перепроверена.',
        }
        Material.objects.get_or_create(
            offer_id=offer.pk, source_filename=audit.source_filename,
            kaspi_product_id=audit.product_id,
            defaults={
                'supplier_url': offer.supplier_url or '',
                'kaspi_url': audit.card_url,
                'supplier_data': supplier_data,
                'supplier_status': 'local_snapshot',
                'supplier_error': 'Сохранённый каталог Omega, не повторная проверка страницы',
                'kaspi_data': {},
                'kaspi_status': 'pending',
                'kaspi_error': 'Применимость, комплектация и характеристики Kaspi требуют подтверждения',
            },
        )


class Migration(migrations.Migration):
    dependencies = [('procurement', '0135_prepare_fifth_hundred_comparisons')]
    operations = [migrations.RunPython(prepare_sixth_batch, migrations.RunPython.noop)]
