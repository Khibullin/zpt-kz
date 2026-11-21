"""Populate source-separated comparison material for fifth batch (items 261–360).

Use only existing Omega data and selected Kaspi audit identity; no prices
or evidence of fitment are manufactured, and existing material is preserved.
"""
from django.db import migrations

FIFTH_IDS = (
    11871,7280,13562,2646,2205,5806,10731,15786,21085,8748,
    7591,13561,1863,2670,13738,15633,4222,17582,5256,5257,
    7787,3076,2767,7641,8308,15636,7350,1742,21112,17406,
    4890,5124,4324,8311,1579,5317,4629,16370,10603,4493,
    15673,5476,5477,13686,337,235,16223,7424,17510,13604,
    8120,12257,17409,7494,17410,13695,18,4990,4991,5146,
    5211,4945,4946,7347,2520,451,2333,7207,1734,17511,
    21098,4561,1107,19487,11802,8679,7374,7224,8252,8124,
    6130,16215,15683,17588,639,2686,7210,11827,2567,148,
    18976,5198,3172,2627,9927,12253,11886,7489,1461,5234,
)

def prepare_fifth_batch(apps, schema_editor):
    Offer = apps.get_model('procurement', 'Offer')
    Audit = apps.get_model('procurement', 'KaspiPriceAudit')
    Material = apps.get_model('procurement', 'BuyingComparisonMaterial')
    for offer in Offer.objects.filter(pk__in=FIFTH_IDS).iterator(chunk_size=200):
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
            'source_note': 'Сохранённые данные каталога Omega; страница поставщика повторно не проверена',
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
                'supplier_error': 'Сохранённый каталог Omega, не повторная проверка сайта',
                'kaspi_data': {},
                'kaspi_status': 'pending',
                'kaspi_error': 'Характеристики Kaspi и соответствие исполнения требуют проверки',
            },
        )

class Migration(migrations.Migration):
    dependencies = [('procurement', '0134_prepare_fourth_hundred_comparisons')]
    operations = [migrations.RunPython(prepare_fifth_batch, migrations.RunPython.noop)]
