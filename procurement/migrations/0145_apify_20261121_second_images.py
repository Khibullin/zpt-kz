"""Preserve exact Kaspi source photos and titles for the 18 Nov-21 Apify review cards.

Only enrich the already existing, unverified 14:00 comparison materials.
The data below is transcribed from exact product IDs in the original 436-card
Apify JSON (file timestamp 2026-11-21 14:00:09). This does not confirm
physical equivalence to an Omega part or establish current Kaspi prices.
Never touch Offer prices, stock, verification flags, buying lists or audits.
"""
from decimal import Decimal
from django.db import migrations

SOURCE = 'dataset_kaspi-api_2026-11-21_14-00-09-125.json'
# offer_id, exact Omega brand/article, exact Kaspi card ID, lowest observed
# Kaspi price in export, card URL, raw source title, raw source image URL.
SNAPSHOTS = [
  [
    5460,
    "STINSS",
    "S2S1552",
    "179049603",
    12784,
    "https://kaspi.kz/shop/p/stinss-perednii-s2s1552-1-sht-179049603/",
    "STINSS передний S2S1552 1 шт",
    "https://resources.cdn-kaspi.kz/img/m/p/pc5/pbe/186003579.jpg?format=preview-large"
  ],
  [
    4584,
    "STINSS",
    "S2S1553",
    "179046414",
    11977,
    "https://kaspi.kz/shop/p/stinss-zadnii-s2s1553-1-sht-179046414/",
    "STINSS задний S2S1553 1 шт",
    "https://resources.cdn-kaspi.kz/img/m/p/p9a/p03/185998264.jpg?format=preview-large"
  ],
  [
    4538,
    "STINSS",
    "S2S1531",
    "179820603",
    12286,
    "https://kaspi.kz/shop/p/stinss-zadnii-s2s1531-1-sht-179820603/",
    "STINSS задний S2S1531 1 шт",
    "https://resources.cdn-kaspi.kz/img/m/p/pb2/pc2/188574869.jpg?format=preview-large"
  ],
  [
    4155,
    "STINSS",
    "S2S1575",
    "179048707",
    12293,
    "https://kaspi.kz/shop/p/stinss-zadnii-s2s1575-1-sht-179048707/",
    "STINSS задний S2S1575 1 шт",
    "https://resources.cdn-kaspi.kz/img/m/p/p0b/pef/186002347.jpg?format=preview-large"
  ],
  [
    5465,
    "STINSS",
    "S2S1554",
    "129584944",
    12937,
    "https://kaspi.kz/shop/p/stinss-perednii-s2s1554-1-sht-129584944/",
    "STINSS передний S2S1554 1 шт",
    "https://resources.cdn-kaspi.kz/img/m/p/p92/p4b/6841531.png?format=preview-large"
  ],
  [
    5493,
    "STINSS",
    "S2S1418",
    "143949415",
    13229,
    "https://kaspi.kz/shop/p/stinss-perednii-s2s1418-1-sht-143949415/",
    "STINSS передний S2S1418 1 шт",
    "https://resources.cdn-kaspi.kz/img/m/p/p46/pe4/58074657.jpg?format=preview-large"
  ],
  [
    59015,
    "Superzing",
    "365757",
    "179042686",
    10763,
    "https://kaspi.kz/shop/p/superzing-zadnii-365757-1-sht-179042686/",
    "Superzing задний 365757 1 шт",
    "https://resources.cdn-kaspi.kz/img/m/p/p09/pe5/185991445.jpg?format=preview-large"
  ],
  [
    5408,
    "STINSS",
    "S2S1493",
    "179047940",
    15861,
    "https://kaspi.kz/shop/p/stinss-perednii-s2s1493-1-sht-179047940/",
    "STINSS передний S2S1493 1 шт",
    "https://resources.cdn-kaspi.kz/img/m/p/pb9/pe4/186000750.jpg?format=preview-large"
  ],
  [
    59602,
    "TMG",
    "706068",
    "174104577",
    12047,
    "https://kaspi.kz/shop/p/tmg-zadnii-706068-1-sht-174104577/",
    "TMG задний 706068 1 шт",
    "https://resources.cdn-kaspi.kz/img/m/p/pbb/pcf/169340287.jpg?format=preview-large"
  ],
  [
    5319,
    "STINSS",
    "S2S1560",
    "115446111",
    15766,
    "https://kaspi.kz/shop/p/stinss-perednii-s2s1560-1-sht-115446111/",
    "STINSS передний S2S1560 1 шт",
    "https://resources.cdn-kaspi.kz/img/m/p/hac/h61/84745588211742.png?format=preview-large"
  ],
  [
    5228,
    "STINSS",
    "S2S1564",
    "143040654",
    15269,
    "https://kaspi.kz/shop/p/stinss-perednii-s2s1564-1-sht-143040654/",
    "STINSS передний S2S1564 1 шт",
    "https://resources.cdn-kaspi.kz/img/m/p/p91/pc1/54876586.jpg?format=preview-large"
  ],
  [
    59042,
    "Superzing",
    "163827",
    "179820627",
    12710,
    "https://kaspi.kz/shop/p/superzing-perednii-163827-1-sht-179820627/",
    "Superzing передний 163827 1 шт",
    "https://resources.cdn-kaspi.kz/img/m/p/p46/pcc/188574893.jpg?format=preview-large"
  ],
  [
    5032,
    "STINSS",
    "S2S1540",
    "143040608",
    16225,
    "https://kaspi.kz/shop/p/stinss-perednii-s2s1540-1-sht-143040608/",
    "STINSS передний S2S1540 1 шт",
    "https://resources.cdn-kaspi.kz/img/m/p/p43/p46/54876432.jpg?format=preview-large"
  ],
  [
    5478,
    "STINSS",
    "S2S1542",
    "143949432",
    15703,
    "https://kaspi.kz/shop/p/stinss-perednii-s2s1542-1-sht-143949432/",
    "STINSS передний S2S1542 1 шт",
    "https://resources.cdn-kaspi.kz/img/m/p/pe0/pc2/58074708.jpg?format=preview-large"
  ],
  [
    59039,
    "Superzing",
    "57409",
    "179042763",
    14005,
    "https://kaspi.kz/shop/p/superzing-perednii-57409-1-sht-179042763/",
    "Superzing передний 57409 1 шт",
    "https://resources.cdn-kaspi.kz/img/m/p/p38/p71/185991573.jpg?format=preview-large"
  ],
  [
    5416,
    "STINSS",
    "S2S1533",
    "142619944",
    18343,
    "https://kaspi.kz/shop/p/stinss-perednii-s2s1533-1-sht-142619944/",
    "STINSS передний S2S1533 1 шт",
    "https://resources.cdn-kaspi.kz/img/m/p/pdc/p4b/53367586.jpg?format=preview-large"
  ],
  [
    5417,
    "STINSS",
    "S2S1534",
    "142298277",
    18343,
    "https://kaspi.kz/shop/p/stinss-perednii-s2s1534-1-sht-142298277/",
    "STINSS передний S2S1534 1 шт",
    "https://resources.cdn-kaspi.kz/img/m/p/pd9/p73/52281656.jpg?format=preview-large"
  ],
  [
    59059,
    "Superzing",
    "57486",
    "152114068",
    15716,
    "https://kaspi.kz/shop/p/superzing-perednii-57486-1-sht-152114068/",
    "Superzing передний 57486 1 шт",
    "https://resources.cdn-kaspi.kz/img/m/p/pd6/pb1/131368081.bin?format=preview-large"
  ]
]

def attach_source_images(apps, schema_editor):
    Offer = apps.get_model('procurement', 'Offer')
    Audit = apps.get_model('procurement', 'KaspiPriceAudit')
    Material = apps.get_model('procurement', 'BuyingComparisonMaterial')
    for offer_id, brand, article, pid, price, url, title, image_url in SNAPSHOTS:
        if not Offer.objects.filter(pk=offer_id, supplier__name='Omega',
                                    brand__iexact=brand, article__iexact=article).exists():
            continue
        # Audit is immutable: require its original exact product and price.
        if not Audit.objects.filter(
                offer_id=offer_id, source_filename=SOURCE, product_id=pid,
                card_url=url, min_price=Decimal(str(price)),
                identity_status='needs_review').exists():
            continue
        material = Material.objects.filter(
            offer_id=offer_id, source_filename=SOURCE,
            kaspi_product_id=pid, kaspi_url=url).first()
        if material is None:
            continue
        existing = material.kaspi_data if isinstance(material.kaspi_data, dict) else {}
        # Respect staff-saved photos/screenshots and verified material; a source
        # thumbnail is evidence only and must not overwrite later human work.
        if existing.get('images') or material.kaspi_status == 'manual_verified':
            continue
        enriched = dict(existing)
        enriched['images'] = [image_url]
        enriched.setdefault('title', title)
        enriched['apify_image_source'] = SOURCE
        enriched['apify_image_product_id'] = pid
        enriched['image_unverified'] = True
        material.kaspi_data = enriched
        material.save(update_fields=['kaspi_data'])

class Migration(migrations.Migration):
    dependencies = [('procurement', '0144_apify_20261121_second_export_review')]
    operations = [migrations.RunPython(attach_source_images, migrations.RunPython.noop)]
