"""Import six independently identifiable 21-Nov-2026 Apify cards into buying review.
Only exact RAMEDER brand + complete article, one-unit packs, positive Omega stock,
supplier cost >= 2000 and conservative net ROI >= 50% enter the catalogue.
No Offer price, verification flag, stock or earlier history is overwritten.
"""
from decimal import Decimal
import re
from django.db import migrations

SOURCE = 'dataset_kaspi-api_2026-11-21_13-39-32-482(2).json'
# article, Kaspi product id, title, min confirmed seller price, photo,
# seller names and their respective observed offer prices in Almaty.
ROWS = (
 ('20C040048','105579482','Rameder задний 20C040048 1 шт',13590,'https://resources.cdn-kaspi.kz/img/m/p/h37/h5e/65052099411998.jpg?format=preview-large',(('RIMS',13590),('Autosklad',14226),('ИП АЛИМЖАНОВА Б.А.',19600))),
 ('20D040022','179824378','Rameder передний 20D040022 1 шт',13995,'https://resources.cdn-kaspi.kz/img/m/p/p4e/p70/188579550.jpg?format=preview-large',(('RIMS',13995),('Sequoia VERS',14470))),
 ('20B040019','115303132','Rameder передний 20B040019 1 шт',14544,'https://resources.cdn-kaspi.kz/img/m/p/h81/h45/84709075648542.png?format=preview-large',(('RIMS',14544),('Autosklad',15026))),
 ('20B040026','105187675','Rameder задний 20B040026 1 шт',15482,'https://resources.cdn-kaspi.kz/img/m/p/h7f/h99/64520805580830.jpg?format=preview-large',(('RIMS',15482),('Autosklad',15752))),
 ('20D040010','179824207','Rameder передний 20D040010 1 шт',18951,'https://resources.cdn-kaspi.kz/img/m/p/p68/pb2/188579325.jpg?format=preview-large',(('RIMS',18951),('Parts Solutions',18952),('Sequoia VERS',21440))),
 ('20A040137','143040612','Rameder передний 20A040137 1 шт',17975,'https://resources.cdn-kaspi.kz/img/m/p/pe5/p49/54876444.jpg?format=preview-large',(('RIMS',17975),('Autosklad',17976))),
)

def apply(apps, schema_editor):
    Offer = apps.get_model('procurement','Offer')
    Candidate = apps.get_model('procurement','KaspiBuyingCandidate')
    Audit = apps.get_model('procurement','KaspiPriceAudit')
    Buying = apps.get_model('procurement','PermanentBuyingItem')
    Material = apps.get_model('procurement','BuyingComparisonMaterial')
    for article, pid, title, minprice, photo, sellers in ROWS:
        offers = list(Offer.objects.filter(
            supplier__name='Omega', brand__iexact='RAMEDER', article__iexact=article))
        if len(offers) != 1:
            continue
        offer = offers[0]
        if not re.match(r'^[1-9]',str(offer.stock or '')) or offer.cost < 2000:
            continue
        if not title.casefold().startswith('rameder ') or article.casefold() not in {
            word.strip('()[]{}:,;').casefold() for word in title.split()
        } or ' 1 шт' not in title.casefold():
            continue
        price = Decimal(minprice)
        if price != min(Decimal(p) for _,p in sellers):
            continue
        commission = Decimal('12.5')
        expenses = Decimal('250')
        profit = price * (Decimal('1')-commission/100) - offer.cost - expenses
        roi = (profit * 100 / offer.cost).quantize(Decimal('0.0001'))
        if roi < 50 or profit <= 0:
            continue
        url = 'https://kaspi.kz/shop/p/' + re.sub(
            r'[^a-z0-9]+','-',title.casefold().replace(' ', '-').replace('передний','perednii').replace('задний','zadnii').replace('шт','sht')
        ).strip('-') + '-' + pid + '/'
        # Keep actual stable Kaspi URL via product identifier; no speculative URL slug.
        url = 'https://kaspi.kz/shop/p/' + (
            ('rameder-zadnii-' if 'задний' in title.casefold() else 'rameder-perednii-')
            + article.casefold() + '-1-sht-' + pid + '/'
        )
        city_offers = [{
            'city':'Almaty','city_id':'750000000','min_price':str(price),
            'sellers':[{'name':name,'price':str(cost)} for name,cost in sellers],
            'sellers_count':len(sellers),
        }]
        # "candidate" records exact textual match; fulfillment/identity remain to be verified.
        candidate, _ = Candidate.objects.get_or_create(product_id=pid,defaults={
            'offer_id':offer.pk,'title':title,'brand':'Rameder','url':url,
            'minimum_price':price,'city_offers':city_offers,'match_status':'candidate',
            'source_filename':SOURCE,
        })
        if candidate.offer_id != offer.pk:
            continue
        Audit.objects.get_or_create(offer_id=offer.pk,source_filename=SOURCE,defaults={
            'min_price':price,'product_id':pid,'card_url':url,'card_title':title,
            'seller_name':min(sellers,key=lambda x:x[1])[0],
            'cost_snapshot':offer.cost,'commission_snapshot':commission,
            'expenses_snapshot':expenses,'profit':profit.quantize(Decimal('0.01')),
            'roi':roi,'card_ids':[pid],'identity_status':'needs_review',
        })
        Buying.objects.get_or_create(offer_id=offer.pk,defaults={
            'source':'apify','product_id':pid,'title':title,'kaspi_url':url,
            'cost_snapshot':offer.cost,'kaspi_price_snapshot':price,
            'commission_snapshot':commission,'expenses_snapshot':expenses,
            'profit_snapshot':profit.quantize(Decimal('0.01')),
            'roi_snapshot':roi,'identity_status':'candidate',
            'photo_url':offer.photo_url or '',
        })
        supplier_photo = str(offer.photo_url or '')
        Material.objects.get_or_create(offer_id=offer.pk,
            source_filename=SOURCE,kaspi_product_id=pid,defaults={
            'supplier_url':offer.supplier_url or '','kaspi_url':url,
            'supplier_data':{
                'brand':offer.brand,'article':offer.article,'name':offer.name,
                'category':offer.category,'description':offer.description or '',
                'fitment':offer.applicability or '',
                'cost':str(offer.cost),'stock':offer.stock,
                'images':[supplier_photo] if supplier_photo.startswith('https://') else [],
                'source_note':'Снимок каталога Omega; наличие требует подтверждения перед закупкой.',
            },
            'supplier_status':'local_snapshot',
            'supplier_error':'Данные Omega из существующего каталога, не повторная проверка.',
            'kaspi_data':{'id':pid,'title':title,'brand':'Rameder',
                'imageUrl':photo,'minPrice':minprice,'city':'Almaty',
                'offers':[{'sellerName':name,'price':cost} for name,cost in sellers]},
            'kaspi_status':'local_snapshot',
            'kaspi_error':'Kaspi API snapshot от 21.11.2026; актуальная цена и исполнение не перепроверены.',
        })

class Migration(migrations.Migration):
    dependencies=[('procurement','0139_prepare_final_153_comparisons')]
    operations=[migrations.RunPython(apply,migrations.RunPython.noop)]
