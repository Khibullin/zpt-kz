from django.db import migrations


SLUG = 'nabor-to-great-wall-poer-20-gw4d20m-two-filters'
ARTICLES = ('1017110XED95', '8100422XNZ01A')


def add_poer_kit(apps, schema_editor):
    Brand = apps.get_model('catalog', 'Brand')
    CarModel = apps.get_model('catalog', 'CarModel')
    Product = apps.get_model('catalog', 'Product')
    MaintenanceKit = apps.get_model('catalog', 'MaintenanceKit')
    MaintenanceKitItem = apps.get_model('catalog', 'MaintenanceKitItem')
    brands = Brand.objects.filter(name='Great Wall')
    products = [Product.objects.filter(article=a, status='active') for a in ARTICLES]
    if brands.count() != 1 or any(q.count() != 1 for q in products):
        return
    brand = brands.get()
    product_ids = [qs.get().pk for qs in products]
    model, _ = CarModel.objects.get_or_create(brand=brand, name='Poer')
    kit, _ = MaintenanceKit.objects.update_or_create(
        slug=SLUG,
        defaults={
            'name': 'Набор ТО — 2 позиции Great Wall Poer 2.0 GW4D20M',
            'brand': brand, 'car_model': model,
            'engine': '2.0 дизель GW4D20M',
            'year_from': 2020, 'year_to': None,
            'description': (
                'Набор ТО — 2 позиции для Great Wall Poer 2.0 дизель '
                'GW4D20M (с сентября 2020): масляный и салонный фильтры. '
                'Воздушный фильтр в набор не входит. Не для Wingle 7 и '
                'других двигателей. Перед заказом сверьте двигатель и '
                'артикулы с VIN.'
            ),
            'cover_note': '', 'reference_lines': [], 'is_active': True,
        },
    )
    for product_id in product_ids:
        MaintenanceKitItem.objects.update_or_create(
            kit=kit, product_id=product_id, defaults={'quantity': 1},
        )
    MaintenanceKitItem.objects.filter(kit=kit).exclude(
        product_id__in=product_ids
    ).delete()


class Migration(migrations.Migration):
    dependencies = [('catalog', '0052_exeed_20_maintenance_kits')]
    operations = [migrations.RunPython(add_poer_kit, migrations.RunPython.noop)]
