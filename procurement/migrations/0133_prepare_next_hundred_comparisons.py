"""Prepare source-separated comparison materials for third batch of 100 Omega offers.

Safe: no price changes, no fabricated Kaspi details, no overwriting prior
manually reviewed or remotely fetched material.
"""
from django.db import migrations

def add_missing_materials(apps, schema_editor):
    from procurement.comparison_next_hundred import NEXT_HUNDRED_OFFER_IDS
    Offer=apps.get_model('procurement','Offer')
    Audit=apps.get_model('procurement','KaspiPriceAudit')
    Material=apps.get_model('procurement','BuyingComparisonMaterial')
    for offer in Offer.objects.filter(pk__in=NEXT_HUNDRED_OFFER_IDS).iterator(chunk_size=200):
        audit=Audit.objects.filter(offer_id=offer.pk).order_by('-observed_at','-pk').first()
        if audit is None or not audit.product_id or not audit.card_url:
            continue
        source={
            'name':offer.name or '',
            'brand':offer.brand or '',
            'article':offer.article or '',
            'category':offer.category or '',
            'description':offer.description or '',
            'fitment':offer.applicability or '',
            'images':[offer.photo_url] if str(offer.photo_url or '').startswith('https://') else [],
            'source_note':'Сохранённые сведения Omega, страница поставщика повторно не проверялась',
        }
        Material.objects.get_or_create(
            offer_id=offer.pk,
            source_filename=audit.source_filename,
            kaspi_product_id=audit.product_id,
            defaults={
                'supplier_url':offer.supplier_url or '',
                'kaspi_url':audit.card_url,
                'supplier_data':source,
                'supplier_status':'local_snapshot',
                'supplier_error':'Данные из сохранённого каталога Omega',
                'kaspi_data':{},
                'kaspi_status':'pending',
                'kaspi_error':'Для характеристик Kaspi нужны подтверждённые материалы',
            },
        )

class Migration(migrations.Migration):
    dependencies=[('procurement','0132_smoke_check_comparison_dates')]
    operations=[migrations.RunPython(add_missing_materials,migrations.RunPython.noop)]
