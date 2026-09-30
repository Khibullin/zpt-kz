from django.db import migrations


KITS = (
    {
        'slug': 'nabor-to-great-wall-wingle-7-two-filters',
        'name': 'Набор ТО — 2 позиции Great Wall Wingle 7',
        'brand': 'Great Wall', 'model': 'Wingle 7', 'engine': '',
        'year_from': None, 'year_to': None,
        'description': (
            'Набор ТО — 2 позиции для Great Wall Wingle 7: воздушный и '
            'салонный фильтры. Масляный фильтр в набор не входит. '
            'Перед заказом сверьте оба артикула с VIN и установленными фильтрами.'
        ),
        'articles': ('1109110XP64XA', '8104400XP24BA'),
    },
    {
        'slug': 'nabor-to-li-auto-l7-two-filters',
        'name': 'Набор ТО — 2 позиции Li Auto L7',
        'brand': 'Li Auto', 'model': 'L7', 'engine': '1.5 L2E15M',
        'year_from': 2022, 'year_to': None,
        'description': (
            'Набор ТО — 2 позиции для Li Auto L7 с бензиновым '
            'генератором 1.5 L2E15M (с сентября 2022): воздушный фильтр '
            'двигателя и салонный фильтр. Перед заказом сверьте артикулы '
            'с VIN и установленными фильтрами.'
        ),
        'articles': ('X01-90000014', 'X0390000206'),
    },
)


def add_two_filter_kits(apps, schema_editor):
    Brand = apps.get_model('catalog', 'Brand')
    CarModel = apps.get_model('catalog', 'CarModel')
    Product = apps.get_model('catalog', 'Product')
    MaintenanceKit = apps.get_model('catalog', 'MaintenanceKit')
    MaintenanceKitItem = apps.get_model('catalog', 'MaintenanceKitItem')
    for spec in KITS:
        brands = Brand.objects.filter(name=spec['brand'])
        parts = [Product.objects.filter(article=a, status='active') for a in spec['articles']]
        if brands.count() != 1 or any(qs.count() != 1 for qs in parts):
            continue
        brand = brands.get()
        model, _ = CarModel.objects.get_or_create(brand=brand, name=spec['model'])
        kit, _ = MaintenanceKit.objects.update_or_create(
            slug=spec['slug'],
            defaults={
                'name': spec['name'], 'brand': brand, 'car_model': model,
                'engine': spec['engine'], 'year_from': spec['year_from'],
                'year_to': spec['year_to'], 'description': spec['description'],
                'cover_note': '', 'reference_lines': [], 'is_active': True,
            },
        )
        product_ids = []
        for qs in parts:
            product = qs.get()
            product_ids.append(product.pk)
            MaintenanceKitItem.objects.update_or_create(
                kit=kit, product=product, defaults={'quantity': 1},
            )
        MaintenanceKitItem.objects.filter(kit=kit).exclude(product_id__in=product_ids).delete()


class Migration(migrations.Migration):
    dependencies = [('catalog', '0049_chery_tiggo_2_maintenance_kit')]
    operations = [migrations.RunPython(add_two_filter_kits, migrations.RunPython.noop)]
