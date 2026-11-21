"""Prepare missing fourth-batch Omega/Kaspi comparisons from existing records.

Preserve prices and earlier verification. We do not fetch Kaspi or pretend
to verify brand, execution, fitment or OEM from a price-search export.
"""
from django.db import migrations

FOURTH_IDS = (
    5561, 15, 16251, 4171, 4124, 5461, 3198, 2558, 5437, 7949,
    5224, 5790, 4370, 397, 8060, 11828, 1568, 3301, 8216, 7963,
    7586, 7628, 3134, 11570, 19449, 8200, 8050, 8048, 962, 9275,
    161, 9530, 8447, 2630, 2661, 1395, 8022, 15797, 670, 7664,
    7665, 2307, 135, 3058, 21128, 11711, 7612, 7559, 7417, 3064,
    19651, 8136, 503, 884, 967, 533, 21115, 2140, 3291, 1298,
    1644, 798, 17506, 11869, 7645, 8039, 2136, 3496, 4334, 7379,
    1094, 15799, 19415, 19413, 2469, 17583, 1343, 7616, 19654, 8066,
    7232, 4898, 17596, 8240, 524, 5550, 2159, 8702, 3099, 17405,
    7398, 426, 7895, 13602, 19360, 11694, 873, 1602, 5838, 17899,
)

def prepare_fourth_batch(apps, schema_editor):
    Offer = apps.get_model('procurement', 'Offer')
    Audit = apps.get_model('procurement', 'KaspiPriceAudit')
    Material = apps.get_model('procurement', 'BuyingComparisonMaterial')
    for offer in Offer.objects.filter(pk__in=FOURTH_IDS).iterator(chunk_size=200):
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
            'source_note': 'Сведения из сохранённого каталога Omega, страница не перепроверена',
        }
        Material.objects.get_or_create(
            offer_id=offer.pk,
            source_filename=audit.source_filename,
            kaspi_product_id=audit.product_id,
            defaults={
                'supplier_url': offer.supplier_url or '',
                'kaspi_url': audit.card_url,
                'supplier_data': supplier_data,
                'supplier_status': 'local_snapshot',
                'supplier_error': 'Сохранённый каталог Omega, без повторной проверки источника',
                'kaspi_data': {},
                'kaspi_status': 'pending',
                'kaspi_error': 'Требуется подтверждение характеристик и исполнения Kaspi',
            },
        )

class Migration(migrations.Migration):
    dependencies = [('procurement', '0133_prepare_next_hundred_comparisons')]
    operations = [migrations.RunPython(prepare_fourth_batch, migrations.RunPython.noop)]
