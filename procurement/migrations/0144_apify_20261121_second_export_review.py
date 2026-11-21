"""Stage price evidence for 18 strict 14:00 Apify matches, NOT publishable products.

Source: 436 Kaspi cards from 218 search queries. 187 strict text matches;
103 offer identities with supplier stock; 18 have conservative ROI >= 50%
after comparing every matching one-piece card at its lowest seller price.
No physical identity, kit contents or fitment was independently confirmed.
Preserve all existing supplier data, historical price/ROI and blocked tasks.
"""
from datetime import datetime, timezone
from decimal import Decimal
import re
from django.db import migrations

SOURCE='dataset_kaspi-api_2026-11-21_14-00-09-125.json'
OBSERVED=datetime(2026,11,21,14,0,9,tzinfo=timezone.utc)
# offer id, brand, article, frozen Omega cost/stock, direction,
# selected cheapest exact Kaspi card ID, observed lowest seller price, source URL.
ROWS=[
 [
  5460,
  "STINSS",
  "S2S1552",
  5603,
  "4",
  "передний",
  "179049603",
  12784,
  "https://kaspi.kz/shop/p/stinss-perednii-s2s1552-1-sht-179049603/"
 ],
 [
  4584,
  "STINSS",
  "S2S1553",
  5341,
  "8",
  "задний",
  "179046414",
  11977,
  "https://kaspi.kz/shop/p/stinss-zadnii-s2s1553-1-sht-179046414/"
 ],
 [
  4538,
  "STINSS",
  "S2S1531",
  5598,
  "20>",
  "задний",
  "179820603",
  12286,
  "https://kaspi.kz/shop/p/stinss-zadnii-s2s1531-1-sht-179820603/"
 ],
 [
  4155,
  "STINSS",
  "S2S1575",
  5604,
  "16",
  "задний",
  "179048707",
  12293,
  "https://kaspi.kz/shop/p/stinss-zadnii-s2s1575-1-sht-179048707/"
 ],
 [
  5465,
  "STINSS",
  "S2S1554",
  6138,
  "18",
  "передний",
  "129584944",
  12937,
  "https://kaspi.kz/shop/p/stinss-perednii-s2s1554-1-sht-129584944/"
 ],
 [
  5493,
  "STINSS",
  "S2S1418",
  6357,
  "1",
  "передний",
  "143949415",
  13229,
  "https://kaspi.kz/shop/p/stinss-perednii-s2s1418-1-sht-143949415/"
 ],
 [
  59015,
  "Superzing",
  "365757",
  5244,
  "20>",
  "задний",
  "179042686",
  10763,
  "https://kaspi.kz/shop/p/superzing-zadnii-365757-1-sht-179042686/"
 ],
 [
  5408,
  "STINSS",
  "S2S1493",
  7946,
  "8",
  "передний",
  "179047940",
  15861,
  "https://kaspi.kz/shop/p/stinss-perednii-s2s1493-1-sht-179047940/"
 ],
 [
  59602,
  "TMG",
  "706068",
  6004,
  "17",
  "задний",
  "174104577",
  12047,
  "https://kaspi.kz/shop/p/tmg-zadnii-706068-1-sht-174104577/"
 ],
 [
  5319,
  "STINSS",
  "S2S1560",
  7946,
  "1",
  "передний",
  "115446111",
  15766,
  "https://kaspi.kz/shop/p/stinss-perednii-s2s1560-1-sht-115446111/"
 ],
 [
  5228,
  "STINSS",
  "S2S1564",
  7695,
  "2",
  "передний",
  "143040654",
  15269,
  "https://kaspi.kz/shop/p/stinss-perednii-s2s1564-1-sht-143040654/"
 ],
 [
  59042,
  "Superzing",
  "163827",
  6395,
  "20>",
  "передний",
  "179820627",
  12710,
  "https://kaspi.kz/shop/p/superzing-perednii-163827-1-sht-179820627/"
 ],
 [
  5032,
  "STINSS",
  "S2S1540",
  8278,
  "8",
  "передний",
  "143040608",
  16225,
  "https://kaspi.kz/shop/p/stinss-perednii-s2s1540-1-sht-143040608/"
 ],
 [
  5478,
  "STINSS",
  "S2S1542",
  8011,
  "1",
  "передний",
  "143949432",
  15703,
  "https://kaspi.kz/shop/p/stinss-perednii-s2s1542-1-sht-143949432/"
 ],
 [
  59039,
  "Superzing",
  "57409",
  7162,
  "20>",
  "передний",
  "179042763",
  14005,
  "https://kaspi.kz/shop/p/superzing-perednii-57409-1-sht-179042763/"
 ],
 [
  5416,
  "STINSS",
  "S2S1533",
  9718,
  "20>",
  "передний",
  "142619944",
  18343,
  "https://kaspi.kz/shop/p/stinss-perednii-s2s1533-1-sht-142619944/"
 ],
 [
  5417,
  "STINSS",
  "S2S1534",
  9718,
  "20>",
  "передний",
  "142298277",
  18343,
  "https://kaspi.kz/shop/p/stinss-perednii-s2s1534-1-sht-142298277/"
 ],
 [
  59059,
  "Superzing",
  "57486",
  8987,
  "20>",
  "передний",
  "152114068",
  15716,
  "https://kaspi.kz/shop/p/superzing-perednii-57486-1-sht-152114068/"
 ]
]

def apply(apps,schema_editor):
    Offer=apps.get_model('procurement','Offer')
    Audit=apps.get_model('procurement','KaspiPriceAudit')
    Material=apps.get_model('procurement','BuyingComparisonMaterial')
    Task=apps.get_model('procurement','KaspiReviewTask')
    for offer_id,brand,article,cost_snapshot,stock_snapshot,direction,pid,minprice,url in ROWS:
        offer=Offer.objects.filter(pk=offer_id,supplier__name='Omega',
                brand__iexact=brand,article__iexact=article).first()
        if not offer or offer.match_verified:
            # Already marked verified elsewhere: leave that workflow alone.
            continue
        if (Decimal(str(offer.cost)) != Decimal(str(cost_snapshot))
            or str(offer.stock) != stock_snapshot
            or not re.match(r'^[1-9]',str(offer.stock or ''))
            or Decimal(str(offer.cost)) < 2000
            or direction not in str(offer.name).casefold()):
            continue
        price=Decimal(str(minprice)); cost=Decimal(str(cost_snapshot))
        profit=price*Decimal('0.875')-cost-Decimal('250')
        roi=(profit*100/cost).quantize(Decimal('0.0001'))
        if roi < 50 or profit <= 0:
            continue
        audit,created=Audit.objects.get_or_create(
            offer_id=offer_id,source_filename=SOURCE,defaults={
            'min_price':price,'product_id':pid,'card_url':url,
            'card_title':'', 'seller_name':'', 'cost_snapshot':cost,
            'commission_snapshot':Decimal('12.5'),
            'expenses_snapshot':Decimal('250'),
            'profit':profit.quantize(Decimal('0.01')),
            'roi':roi,'card_ids':[pid],'identity_status':'needs_review',
        })
        if created:
            # The export was captured earlier than this database migration.
            Audit.objects.filter(pk=audit.pk).update(observed_at=OBSERVED)
        Material.objects.get_or_create(offer_id=offer_id,
            source_filename=SOURCE,kaspi_product_id=pid,defaults={
            'supplier_url':offer.supplier_url or '',
            'kaspi_url':url,
            'supplier_data':{
                'name':offer.name,'brand':offer.brand,'article':offer.article,
                'category':offer.category,'cost':str(cost),'stock':offer.stock,
                'images':[offer.photo_url] if str(offer.photo_url or '').startswith('https://') else [],
                'source_note':'Снимок Omega, без повторного подтверждения исполнения.'},
            'kaspi_data':{
                'id':pid,'brand':brand,'article':article,'package':'1 шт',
                'images':[],'minPrice':str(price),'city':'Almaty',
                'source_note':'Текстовые артикул/бренд/штучность совпали в файле Apify. Фото и применимость нуждаются в независимой проверке.'},
            'supplier_status':'local_snapshot','kaspi_status':'local_snapshot',
            'supplier_error':'Текущие цена и наличие Omega не перепроверены.',
            'kaspi_error':'Снимок Apify от 21.11.2026. Физическое совпадение товара не подтверждено.',
        })
        task,new=Task.objects.get_or_create(offer_id=offer_id,defaults={
            'priority':1,'status':'pending',
            'reason':f'Apify 21.11 14:00: Kaspi {pid}, ROI {roi:.1f}%. Бренд и артикул совпадают; исполнение и применимость проверить до публикации.',
        })
        if not new and task.status == 'pending' and task.reason == 'Найти и проверить карточку Kaspi':
            task.reason=(f'Apify 21.11 14:00: Kaspi {pid}, ROI {roi:.1f}%. '
                'Бренд/артикул совпадают; требуется проверка исполнения до публикации.')
            task.priority=1
            task.save(update_fields=['reason','priority','updated_at'])
        # IMPORTANT: blocked HTTP 429 reviews are never restarted here.
        # Neither PermanentBuyingItem nor Offer prices, stock or verification
        # are modified. Price audit is immutable after initial insert.

class Migration(migrations.Migration):
    dependencies=[('procurement','0143_apify_brand_review_queue_20261121')]
    operations=[migrations.RunPython(apply,migrations.RunPython.noop)]
