from django.db import migrations


PROFILE_FACTS = {
    470: {
        'name': 'Автотрейд',
        'phone': '77719499204',
        'website': 'https://autotrade.kz/',
        'address': 'Шымкент, ул. Байтулы Баба, 18',
    },
    472: {
        'name': 'Takama',
        'phone': '77760502525',
        'address': 'Шымкент, ул. Рыскулова, 93а',
    },
    474: {
        'name': 'Shahs auto service',
        'phone': '77089098048',
        'website': 'https://shahs-auto-service.kz/',
        'address': 'Шымкент, ул. Есениязова, 57',
    },
    480: {
        'name': 'ЦентрАвтоГаз',
        'phone': '77017878726',
        'address': 'Шымкент, ул. Капал батыра, 54/9',
    },
    486: {
        'name': 'Chery Crystal Shymkent',
        'phone': '77475030303',
        'website': 'https://chery-crystal.kz/',
        'instagram': 'https://www.instagram.com/chery.shymkent.crystal/',
        'address': 'Шымкент, Темирлановская трасса, 177а',
    },
    492: {
        'name': 'Ravon Shymkent',
        'phone': '77053715111',
        'address': 'Шымкент, микрорайон Восток, 93',
    },
    498: {
        'name': 'BYD LuxCar Shymkent',
        'phone': '77018777999',
        'website': 'https://byd-luxcar-shymkent.kz/',
        'address': 'Шымкент, пр. Тауке хана, 330/2',
    },
    503: {
        'name': 'One Market',
        'phone': '77001930303',
        'website': 'https://onemarket.kz/',
        'address': 'Шымкент, Жибек Жолы, 78/14',
    },
    504: {
        'name': 'Немец',
        'phone': '77054884800',
        'address': 'Шымкент, ул. Жас Гвардияшы, 132',
    },
    505: {
        'name': 'M Bavaria',
        'phone': '77005055580',
        'address': 'Шымкент, ул. Сайрамская, 167',
    },
    509: {
        'name': 'Реал-Авто',
        'phone': '77013220313',
        'address': 'Шымкент, ул. Елшибек батыра, 86',
    },
    513: {
        'name': 'Headlamp',
        'phone': '77017787947',
        'website': 'https://headlamp.kz/',
    },
    514: {
        'name': 'IGS Petronas oil service',
        'phone': '77025555594',
        'address': 'Шымкент, ул. Мадели кожа, 183',
    },
    516: {
        'name': 'Tanauto Shymkent',
        'phone': '77084787327',
        'website': 'https://tanauto.kz/',
        'address': 'Шымкент, ул. Желтоксан, 233, уг. Аксу',
    },
    519: {
        'name': 'Subaru Market KZ',
        'phone': '77015556964',
        'address': 'Шымкент, ул. Казыбек би, 192/1',
    },
    520: {
        'name': 'Sardor',
        'phone': '77758300007',
        'address': 'Шымкент, ул. Ибрагим ата, 113/20',
    },
    523: {
        'name': 'Manta service',
        'phone': '77008884400',
        'address': 'Шымкент, ул. Сайрам, 198Б',
    },
    530: {
        'name': 'Cobalt_Shymkent',
        'phone': '77771000197',
        'address': 'Шымкент, ул. Акдала, 2/1',
    },
    533: {
        'name': 'Газ',
        'phone': '77765955500',
        'address': 'Шымкент, ул. Кожанова, 54',
    },
}


def normalize_phone(value):
    digits = ''.join(ch for ch in (value or '') if ch.isdigit())
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    if len(digits) == 10:
        digits = '7' + digits
    return digits


def apply_facts(apps, schema_editor):
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


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0061_registered_seller_public_audit'),
    ]

    operations = [
        migrations.RunPython(apply_facts, migrations.RunPython.noop),
    ]
