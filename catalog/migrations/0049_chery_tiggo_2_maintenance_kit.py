from django.db import migrations


SLUG = 'nabor-to-chery-tiggo-2-15-sqrd4g15b'


def add_tiggo_2_kit(apps, schema_editor):
    Brand = apps.get_model('catalog', 'Brand')
    CarModel = apps.get_model('catalog', 'CarModel')
    Product = apps.get_model('catalog', 'Product')
    MaintenanceKit = apps.get_model('catalog', 'MaintenanceKit')
    MaintenanceKitItem = apps.get_model('catalog', 'MaintenanceKitItem')
    brands = Brand.objects.filter(name='Chery')
    air = Product.objects.filter(article='J691109111', status='active')
    cabin = Product.objects.filter(article='A138107915', status='active')
    if brands.count() != 1 or air.count() != 1 or cabin.count() != 1:
        # Missing or ambiguous inventory should never result in a public kit.
        return
    brand = brands.get()
    model, _ = CarModel.objects.get_or_create(brand=brand, name='Tiggo 2')
    kit, _ = MaintenanceKit.objects.update_or_create(
        slug=SLUG,
        defaults={
            'name': 'Набор ТО — 2 позиции Chery Tiggo 2 1.5 SQRD4G15B',
            'brand': brand,
            'car_model': model,
            'engine': '1.5 SQRD4G15B',
            'year_from': 2017,
            'year_to': 2020,
            'description': (
                'Набор ТО — 2 позиции для Chery Tiggo 2 1.5 SQRD4G15B '
                '(DB11B2H, март 2017 – январь 2020): воздушный и салонный '
                'фильтры. Масляный фильтр и свечи в набор не входят. '
                'Перед заказом сверьте модификацию и VIN.'
            ),
            'cover_note': '',
            'reference_lines': [],
            'is_active': True,
        },
    )
    for product in (air.get(), cabin.get()):
        MaintenanceKitItem.objects.update_or_create(
            kit=kit, product=product, defaults={'quantity': 1},
        )
    MaintenanceKitItem.objects.filter(kit=kit).exclude(
        product_id__in=(air.get().pk, cabin.get().pk)
    ).delete()


class Migration(migrations.Migration):
    dependencies = [('catalog', '0048_price_four_verified_products_20260930')]
    operations = [migrations.RunPython(add_tiggo_2_kit, migrations.RunPython.noop)]
