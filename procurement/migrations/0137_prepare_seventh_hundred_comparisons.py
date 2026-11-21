"""Admit seventh-batch leads into append-only review register and prepare details.

Evidence: latest Apify seller-price audit, Omega catalog article and positive stock.
These are marked 'candidate' and cannot imply verified compatibility.
No previous buying item, audit, offer, or pricing field is updated.
"""
from decimal import Decimal
import re
from django.db import migrations

BATCH_IDS = (
    9264, 9191, 380, 8996, 16440, 18316, 9513, 17107, 17271, 4720,
    3315, 16467, 16443, 280, 3250, 18269, 16828, 9592, 4665, 4782,
    4653, 18063, 3442, 3576, 17090, 4716, 13525, 9531, 19144, 19145,
    16668, 10034, 5793, 18915, 14061, 19209, 16431, 19616, 18067, 17091,
    17051, 19320, 14056, 11315, 11740, 20730, 8634, 8763, 17870, 9269,
    8840, 5121, 16519, 19567, 19534, 19600, 19425, 16661, 16662, 19577,
    20883, 9407, 18326, 16762, 16434, 16923, 2750, 2437, 16809, 20863,
    16663, 20925, 19030, 3237, 9540, 16506, 9092, 17110, 16769, 16776,
    16516, 793, 1321, 19338, 16512, 13453, 3570, 20750, 1634, 8883,
    16508, 16780, 18901, 16857, 16435, 603, 8994, 19198, 20541, 16726,
)


def exact_title_match(title, brand, article):
    if not title or not brand or not article:
        return False
    # Whole tokens, including full alphanumeric article (preserves leading 0).
    words = re.findall(r'[\w]+(?:[-./][\w]+)*', title.casefold(), re.UNICODE)
    parts = re.findall(r'[\w]+', brand.casefold(), re.UNICODE)
    return article.casefold() in words and all(part in words for part in parts)


def prepare_seventh_batch(apps, schema_editor):
    Offer = apps.get_model('procurement', 'Offer')
    Audit = apps.get_model('procurement', 'KaspiPriceAudit')
    Buying = apps.get_model('procurement', 'PermanentBuyingItem')
    Material = apps.get_model('procurement', 'BuyingComparisonMaterial')
    for offer in Offer.objects.select_related('supplier').filter(pk__in=BATCH_IDS).iterator(chunk_size=200):
        audit = Audit.objects.filter(offer_id=offer.pk).order_by('-observed_at', '-pk').first()
        if (audit is None or offer.supplier.name != 'Omega' or
                not audit.product_id or not (audit.card_url or '').startswith('https://kaspi.kz/shop/p/') or
                audit.cost_snapshot < Decimal('2000') or audit.roi < Decimal('50') or
                audit.profit <= 0 or not re.match(r'^[1-9]', str(offer.stock or '')) or
                not exact_title_match(audit.card_title, offer.brand, offer.article)):
            continue

        # Finance snapshot copied from the actual seller-price audit, not
        # independently inferred, and labelled preliminary until reverified.
        Buying.objects.get_or_create(offer_id=offer.pk, defaults={
            'source': 'apify',
            'product_id': audit.product_id,
            'title': audit.card_title or (offer.brand + ' ' + offer.article),
            'kaspi_url': audit.card_url,
            'cost_snapshot': audit.cost_snapshot,
            'kaspi_price_snapshot': audit.min_price,
            'commission_snapshot': audit.commission_snapshot,
            'expenses_snapshot': audit.expenses_snapshot,
            'profit_snapshot': audit.profit,
            'roi_snapshot': audit.roi,
            'identity_status': 'candidate',
            'photo_url': offer.photo_url or '',
        })
        photo = str(offer.photo_url or '')
        Material.objects.get_or_create(
            offer_id=offer.pk, source_filename=audit.source_filename,
            kaspi_product_id=audit.product_id,
            defaults={
                'supplier_url': offer.supplier_url or '',
                'kaspi_url': audit.card_url,
                'supplier_data': {
                    'name': offer.name or '',
                    'brand': offer.brand or '',
                    'article': offer.article or '',
                    'category': offer.category or '',
                    'description': offer.description or '',
                    'fitment': offer.applicability or '',
                    'images': [photo] if photo.startswith('https://') else [],
                    'source_note': 'Сохранённые сведения Omega, страница поставщика не перепроверена',
                },
                'supplier_status': 'local_snapshot',
                'supplier_error': 'Материал из сохранённого каталога Omega',
                'kaspi_data': {},
                'kaspi_status': 'pending',
                'kaspi_error': 'Предварительное совпадение по названию: требуется проверка исполнения',
            },
        )


class Migration(migrations.Migration):
    dependencies = [('procurement', '0136_prepare_sixth_hundred_comparisons')]
    operations = [migrations.RunPython(prepare_seventh_batch, migrations.RunPython.noop)]
