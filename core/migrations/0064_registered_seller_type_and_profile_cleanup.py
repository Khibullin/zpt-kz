from django.db import migrations


PROFILE_FACTS = {
    483: {
        'name': 'Hyundai x Genesis Shymkent',
        'phone': '77252600001',
        'website': 'https://hyundai-shymkent.kz/',
        'address': 'Шымкент, Темирлановское шоссе, 90А',
    },
    502: {
        'name': 'SubaRus',
        'phone': '77017268604',
        'address': 'Шымкент, ул. Толстого, 152',
    },
    507: {
        'name': 'Alatrade',
        'phone': '78000807602',
        'website': 'https://alatrade.kz/',
        'address': 'Шымкент, ул. Толе Би, 41/5',
    },
}

SELLER_TYPE_FIXES = {
    474: ('Shahs auto service', '77089098048', 'both'),
    480: ('ЦентрАвтоГаз', '77017878726', 'both'),
    483: ('Hyundai x Genesis Shymkent', '77252600001', 'both'),
    486: ('Chery Crystal Shymkent', '77475030303', 'both'),
    498: ('BYD LuxCar Shymkent', '77018777999', 'both'),
    503: ('One Market', '77001930303', 'both'),
    505: ('M Bavaria', '77005055580', 'both'),
    513: ('Headlamp', '77017787947', 'both'),
    520: ('Sardor', '77758300007', 'both'),
    523: ('Manta service', '77008884400', 'service'),
    533: ('Газ', '77765955500', 'both'),
}


def normalize_phone(value):
    digits = ''.join(ch for ch in (value or '') if ch.isdigit())
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    if len(digits) == 10:
        digits = '7' + digits
    return digits


def apply_cleanup(apps, schema_editor):
    Seller = apps.get_model('core', 'Seller')
    SellerProfile = apps.get_model('catalog', 'SellerProfile')

    for seller_id, fact in PROFILE_FACTS.items():
        seller = Seller.objects.filter(pk=seller_id, user_id__isnull=False).first()
        if seller is None:
            continue
        if seller.name.strip().casefold() != fact['name'].strip().casefold():
            continue
        if normalize_phone(seller.whatsapp) != fact['phone']:
            continue
        profile = SellerProfile.objects.filter(user_id=seller.user_id).first()
        if profile is None:
            continue
        fields = []
        for field_name in ('website', 'instagram', 'address'):
            value = fact.get(field_name, '')
            if value and not (getattr(profile, field_name, '') or '').strip():
                setattr(profile, field_name, value)
                fields.append(field_name)
        if fields:
            profile.save(update_fields=fields)

    for seller_id, (name, phone, seller_type) in SELLER_TYPE_FIXES.items():
        seller = Seller.objects.filter(pk=seller_id, user_id__isnull=False).first()
        if seller is None:
            continue
        if seller.name.strip().casefold() != name.strip().casefold():
            continue
        if normalize_phone(seller.whatsapp) != phone:
            continue
        if seller.seller_type != seller_type:
            seller.seller_type = seller_type
            seller.save(update_fields=['seller_type'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0063_registered_seller_brand_cleanup'),
    ]

    operations = [
        migrations.RunPython(apply_cleanup, migrations.RunPython.noop),
    ]
