"""Refresh three existing unverified Apify cards from the 21-Nov seller snapshot.
Preserve the 18-Nov records and the new immutable KaspiPriceAudit evidence.
"""
from decimal import Decimal
from django.db import migrations

SOURCE='dataset_kaspi-api_2026-11-21_13-39-32-482(2).json'
ROWS=(
 ('20C040048','105579482',13590,(('RIMS',13590),('Autosklad',14226),('ИП АЛИМЖАНОВА Б.А.',19600))),
 ('20B040019','115303132',14544,(('RIMS',14544),('Autosklad',15026))),
 ('20A040137','143040612',17975,(('RIMS',17975),('Autosklad',17976))),
)

def apply(apps,schema_editor):
    Offer=apps.get_model('procurement','Offer')
    Candidate=apps.get_model('procurement','KaspiBuyingCandidate')
    Audit=apps.get_model('procurement','KaspiPriceAudit')
    for article,product_id,amount,sellers in ROWS:
        offers=list(Offer.objects.filter(
            supplier__name='Omega',brand__iexact='RAMEDER',article__iexact=article))
        if len(offers)!=1:
            continue
        offer=offers[0]
        if offer.cost<Decimal('2000') or not str(offer.stock or '').startswith(tuple('123456789')):
            continue
        audit=Audit.objects.filter(offer_id=offer.pk,source_filename=SOURCE,
            product_id=product_id,min_price=Decimal(amount),roi__gte=50).first()
        if audit is None:
            continue
        card=Candidate.objects.filter(product_id=product_id,offer_id=offer.pk,
            match_status='candidate').first()
        if card is None:
            continue
        card.minimum_price=Decimal(amount)
        card.source_filename=SOURCE
        card.city_offers=[{
            'city':'Almaty','city_id':'750000000',
            'min_price':str(amount),
            'sellers':[{'name':name,'price':str(price)} for name,price in sellers],
            'sellers_count':len(sellers),
        }]
        card.save(update_fields=['minimum_price','source_filename','city_offers','imported_at'])

class Migration(migrations.Migration):
    dependencies=[('procurement','0140_import_apify_20261121_strict_six')]
    operations=[migrations.RunPython(apply,migrations.RunPython.noop)]
