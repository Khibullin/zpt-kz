"""Prepare final 153 Omega buying comparisons without claiming Kaspi verification.

The mismatched 154th card is not admitted. Existing permanent records and
all source price audits are never modified.
"""
from decimal import Decimal
import re
from django.db import migrations


def title_matches(title, brand, article):
    if not (title and brand and article):
        return False
    tokens = re.findall(r'[\w]+(?:[-./][\w]+)*', title.casefold(), re.UNICODE)
    brands = re.findall(r'[\w]+', brand.casefold(), re.UNICODE)
    return article.casefold() in tokens and all(b in tokens for b in brands)


def prepare_remaining(apps, schema_editor):
    from procurement.comparison_final_remaining import FINAL_REMAINING_OFFER_IDS
    Offer = apps.get_model('procurement', 'Offer')
    Audit = apps.get_model('procurement', 'KaspiPriceAudit')
    Buying = apps.get_model('procurement', 'PermanentBuyingItem')
    Material = apps.get_model('procurement', 'BuyingComparisonMaterial')

    for offer in Offer.objects.select_related('supplier').filter(
            pk__in=FINAL_REMAINING_OFFER_IDS).iterator(chunk_size=200):
        audit = Audit.objects.filter(offer_id=offer.pk).order_by('-observed_at','-pk').first()
        if (audit is None or offer.supplier.name != 'Omega' or
                not audit.product_id or
                not (audit.card_url or '').startswith('https://kaspi.kz/shop/p/') or
                audit.cost_snapshot < Decimal('2000') or
                audit.roi < Decimal('50') or audit.profit <= 0 or
                not re.match(r'^[1-9]', str(offer.stock or '')) or
                not title_matches(audit.card_title, offer.brand, offer.article)):
            continue
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
                    'source_note': 'Сохранённые сведения Omega; нет повторной проверки сайта',
                },
                'supplier_status': 'local_snapshot',
                'supplier_error': 'Из сохранённого каталога Omega',
                'kaspi_data': {},
                'kaspi_status': 'pending',
                'kaspi_error': 'По бренду и артикулу предварительно совпадает; необходимо подтвердить исполнение',
            },
        )


class Migration(migrations.Migration):
    dependencies = [('procurement', '0138_prepare_eighth_two_hundred_comparisons')]
    operations = [migrations.RunPython(prepare_remaining, migrations.RunPython.noop)]
