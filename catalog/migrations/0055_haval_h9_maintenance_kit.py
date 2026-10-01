from django.db import migrations


SLUG = 'nabor-to-haval-h9-20-gw4c20a-two-filters'
ARTICLES = ('1109110XKV08A', '8100103XKV08A')


def add_h9_kit(apps, schema_editor):
    Brand = apps.get_model('catalog', 'Brand')
    CarModel = apps.get_model('catalog', 'CarModel')
    Product = apps.get_model('catalog', 'Product')
    MaintenanceKit = apps.get_model('catalog', 'MaintenanceKit')
    MaintenanceKitItem = apps.get_model('catalog', 'MaintenanceKitItem')
    brands = Brand.objects.filter(name='Haval')
    products = [Product.objects.filter(article=a, status='active') for a in ARTICLES]
    if brands.count() != 1 or any(q.count() != 1 for q in products):
        return
    brand = brands.get()
    product_ids = [qs.get().pk for qs in products]
    model, _ = CarModel.objects.get_or_create(brand=brand, name='H9')
    kit, _ = MaintenanceKit.objects.update_or_create(
        slug=SLUG,
        defaults={
            'name': 'Набор ТО — 2 позиции Haval H9 2.0 GW4C20A',
            'brand': brand, 'car_model': model,
            'engine': '2.0 GW4C20A',
            'year_from': 2017, 'year_to': 2019,
            'description': (
                'Набор ТО — 2 позиции для Haval H9 2.0 GW4C20A '
                '(кузов CC6490WM, ноябрь 2017 – сентябрь 2019): воздушный '
                'фильтр двигателя и салонный фильтр. Масляный фильтр в набор '
                'не входит. Для других двигателей и годов применяемость не '
                'подтверждена. Перед заказом сверьте артикулы с VIN и '
                'установленными фильтрами.'
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
    dependencies = [('catalog', '0054_sync_kaspi_prices_20261001')]
    operations = [migrations.RunPython(add_h9_kit, migrations.RunPython.noop)]
