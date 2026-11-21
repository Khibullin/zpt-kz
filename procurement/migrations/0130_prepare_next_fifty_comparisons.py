"""Prepare missing Omega source snapshots for the second 50-card review batch.

No remote scraping, no speculative Kaspi attributes, and no modifications to
offers, price audits or already enriched comparison records.
"""
from django.db import migrations

BATCH_IDS = (7981, 8138, 13566, 5551, 7986, 15706, 11780, 15627, 8139, 19372, 15796, 11801, 15630, 15803, 15731, 4978, 11808, 5448, 18750, 15779, 18765, 18764, 15628, 15725, 15724, 8659, 15714, 15775, 4866, 4930, 5264, 17559, 18751, 7270, 17751, 10537, 4606, 7644, 13861, 8635, 10601, 8260, 19473, 8764, 5554, 3106, 19447, 10597, 17566, 17565,)


def prepare_missing_comparisons(apps, schema_editor):
    Offer = apps.get_model('procurement', 'Offer')
    Audit = apps.get_model('procurement', 'KaspiPriceAudit')
    Material = apps.get_model('procurement', 'BuyingComparisonMaterial')
    for offer in Offer.objects.filter(pk__in=BATCH_IDS).iterator():
        audit = Audit.objects.filter(offer_id=offer.pk).order_by('-observed_at', '-pk').first()
        if not audit or not audit.product_id:
            continue
        # An existing material, whether verified or an unavailable HTTP source,
        # is deliberately never overwritten.
        if Material.objects.filter(
            offer_id=offer.pk, source_filename=audit.source_filename,
            kaspi_product_id=audit.product_id,
        ).exists():
            continue
        images = [offer.photo_url] if str(offer.photo_url or '').startswith('https://') else []
        source = {
            'name': offer.name or '',
            'brand': offer.brand or '',
            'article': offer.article or '',
            'category': offer.category or '',
            'description': offer.description or '',
            'fitment': offer.applicability or '',
            'images': images,
            'source_note': 'Из сохранённого каталога Omega; сайт поставщика повторно не проверялся.',
        }
        Material.objects.get_or_create(
            offer_id=offer.pk, source_filename=audit.source_filename,
            kaspi_product_id=audit.product_id,
            defaults={
                'supplier_url': offer.supplier_url or '',
                'kaspi_url': audit.card_url or '',
                'supplier_data': source,
                'supplier_status': 'local_snapshot',
                'supplier_error': 'Источник: сохранённый каталог Omega, не повторная проверка страницы',
                'kaspi_data': {},
                'kaspi_status': 'pending',
                'kaspi_error': 'Характеристики и изображение Kaspi требуют подтверждения',
            },
        )


class Migration(migrations.Migration):
    dependencies = [('procurement', '0129_verify_kaspi_151099201_screenshot')]
    operations = [migrations.RunPython(
        prepare_missing_comparisons, migrations.RunPython.noop)]
