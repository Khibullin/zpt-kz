"""Expose original Apify Kaspi product photos in the right comparison column."""
from django.db import migrations

SOURCE='dataset_kaspi-api_2026-11-21_13-39-32-482(2).json'
PHOTOS=[["20C040048", "105579482", "https://resources.cdn-kaspi.kz/img/m/p/h37/h5e/65052099411998.jpg?format=preview-large"], ["20D040022", "179824378", "https://resources.cdn-kaspi.kz/img/m/p/p4e/p70/188579550.jpg?format=preview-large"], ["20B040019", "115303132", "https://resources.cdn-kaspi.kz/img/m/p/h81/h45/84709075648542.png?format=preview-large"], ["20B040026", "105187675", "https://resources.cdn-kaspi.kz/img/m/p/h7f/h99/64520805580830.jpg?format=preview-large"], ["20D040010", "179824207", "https://resources.cdn-kaspi.kz/img/m/p/p68/pb2/188579325.jpg?format=preview-large"], ["20A040137", "143040612", "https://resources.cdn-kaspi.kz/img/m/p/pe5/p49/54876444.jpg?format=preview-large"]]

def apply(apps,schema_editor):
    Material=apps.get_model('procurement','BuyingComparisonMaterial')
    Offer=apps.get_model('procurement','Offer')
    for article,pid,photo in PHOTOS:
        offer=Offer.objects.filter(supplier__name='Omega',brand__iexact='RAMEDER',
                                  article__iexact=article).first()
        if not offer: continue
        item=Material.objects.filter(offer_id=offer.pk,source_filename=SOURCE,
                                     kaspi_product_id=pid).first()
        if item is None: continue
        existing=item.kaspi_data if isinstance(item.kaspi_data,dict) else {}
        prior=existing.get('images')
        if not prior:
            item.kaspi_data={**existing,'images':[photo]}
            item.save(update_fields=['kaspi_data','checked_at'])

class Migration(migrations.Migration):
    dependencies=[('procurement','0141_update_existing_apify_cards_20261121')]
    operations=[migrations.RunPython(apply,migrations.RunPython.noop)]
