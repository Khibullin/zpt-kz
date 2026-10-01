from django.db import migrations


def add_m11_kit(apps, schema_editor):
    Brand = apps.get_model('catalog', 'Brand')
    CarModel = apps.get_model('catalog', 'CarModel')
    Product = apps.get_model('catalog', 'Product')
    MaintenanceKit = apps.get_model('catalog', 'MaintenanceKit')
    MaintenanceKitItem = apps.get_model('catalog', 'MaintenanceKitItem')

    brands = Brand.objects.filter(name='Chery')
    articles = ('M111109111', 'M118107915')
    products = [Product.objects.filter(article=a, status='active') for a in articles]
    if brands.count() != 1 or any(q.count() != 1 for q in products):
        return
    brand = brands.get()
    model, _ = CarModel.objects.get_or_create(brand=brand, name='M11')
    kit, _ = MaintenanceKit.objects.update_or_create(
        slug='nabor-to-chery-m11-16-two-filters',
        defaults={
            'name': 'Набор ТО — 2 позиции Chery M11 1.6 SQRD4G16',
            'brand': brand,
            'car_model': model,
            'engine': '1.6 SQRD4G16',
            'year_from': 2010,
            'year_to': None,
            'description': (
                'Набор ТО — 2 позиции для Chery M11 1.6 SQRD4G16: '
                'воздушный фильтр двигателя и салонный фильтр. '
                'Масляный фильтр в набор не входит. Перед заказом сверьте '
                'артикулы с VIN и установленными фильтрами. Не для Jetour X70.'
            ),
            'cover_note': '',
            'reference_lines': [],
            'is_active': True,
        },
    )
    ids = []
    for qs in products:
        product = qs.get()
        ids.append(product.pk)
        MaintenanceKitItem.objects.update_or_create(
            kit=kit, product=product, defaults={'quantity': 1},
        )
    MaintenanceKitItem.objects.filter(kit=kit).exclude(product_id__in=ids).delete()


class Migration(migrations.Migration):
    dependencies = [('catalog', '0050_wingle7_li_auto_l7_maintenance_kits')]
    operations = [migrations.RunPython(add_m11_kit, migrations.RunPython.noop)]
