from django.db import migrations


BRAND_FIXES = {
    479: {
        'name': 'Changan Luxcar',
        'phone': '77780215545',
        'brands': ('Changan',),
    },
    483: {
        'name': 'Hyundai x Genesis Shymkent',
        'phone': '77252600001',
        'brands': ('Hyundai', 'Genesis'),
    },
    485: {
        'name': 'Toyota Center Shymkent',
        'phone': '77010063499',
        'brands': ('Toyota',),
    },
    486: {
        'name': 'Chery Crystal Shymkent',
        'phone': '77475030303',
        'brands': ('Chery',),
    },
    492: {
        'name': 'Ravon Shymkent',
        'phone': '77053715111',
        'brands': ('Daewoo', 'Chevrolet'),
    },
    495: {
        'name': 'Haval Luxcar Shymkent',
        'phone': '77787463219',
        'brands': ('Haval',),
    },
    498: {
        'name': 'BYD LuxCar Shymkent',
        'phone': '77018777999',
        'brands': ('BYD',),
    },
    502: {
        'name': 'SubaRus',
        'phone': '77017268604',
        'brands': ('Subaru',),
    },
    504: {
        'name': 'Немец',
        'phone': '77054884800',
        'brands': ('BMW',),
    },
    506: {
        'name': 'Tank Luxcar Shymkent',
        'phone': '77780468541',
        'brands': ('Tank',),
    },
    508: {
        'name': 'Subaru 17',
        'phone': '77024326060',
        'brands': ('Subaru',),
    },
    519: {
        'name': 'Subaru Market KZ',
        'phone': '77015556964',
        'brands': ('Subaru',),
    },
    530: {
        'name': 'Cobalt_Shymkent',
        'phone': '77771000197',
        'brands': ('Chevrolet',),
    },
    533: {
        'name': 'Газ',
        'phone': '77765955500',
        'brands': ('GAZ',),
    },
    539: {
        'name': 'Lada vaz 077',
        'phone': '77750802929',
        'brands': ('Lada',),
    },
}


def normalize_phone(value):
    digits = ''.join(ch for ch in (value or '') if ch.isdigit())
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    if len(digits) == 10:
        digits = '7' + digits
    return digits


def fix_specialized_seller_brands(apps, schema_editor):
    Seller = apps.get_model('core', 'Seller')
    Brand = apps.get_model('catalog', 'Brand')

    for seller_id, fact in BRAND_FIXES.items():
        seller = Seller.objects.filter(pk=seller_id, user_id__isnull=False).first()
        if seller is None:
            continue
        if seller.name.strip().casefold() != fact['name'].strip().casefold():
            continue
        if normalize_phone(seller.whatsapp) != fact['phone']:
            continue
        brands = list(Brand.objects.filter(name__in=fact['brands']))
        if {brand.name for brand in brands} != set(fact['brands']):
            continue
        seller.selected_brands.set(brands)
        if seller.all_brands:
            seller.all_brands = False
            seller.save(update_fields=['all_brands'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0062_registered_seller_directory_cleanup'),
    ]

    operations = [
        migrations.RunPython(fix_specialized_seller_brands, migrations.RunPython.noop),
    ]
