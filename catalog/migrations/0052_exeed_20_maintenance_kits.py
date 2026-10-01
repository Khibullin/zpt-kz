from django.db import migrations


SPECS = (
    ('TXL', 'M32T', 2022, 'nabor-to-exeed-txl-20-sqrf4j20c-two-filters'),
    ('VX', 'M36T', 2020, 'nabor-to-exeed-vx-20-sqrf4j20c-two-filters'),
)


def add_exeed_20_kits(apps, schema_editor):
    Brand = apps.get_model('catalog', 'Brand')
    CarModel = apps.get_model('catalog', 'CarModel')
    Product = apps.get_model('catalog', 'Product')
    MaintenanceKit = apps.get_model('catalog', 'MaintenanceKit')
    MaintenanceKitItem = apps.get_model('catalog', 'MaintenanceKitItem')
    brands = Brand.objects.filter(name='Exeed')
    articles = ('151000187AA', '301000265AA')
    products = [Product.objects.filter(article=a, status='active') for a in articles]
    if brands.count() != 1 or any(q.count() != 1 for q in products):
        return
    brand = brands.get()
    product_ids = [qs.get().pk for qs in products]
    for model_name, body, year, slug in SPECS:
        model, _ = CarModel.objects.get_or_create(brand=brand, name=model_name)
        kit, _ = MaintenanceKit.objects.update_or_create(
            slug=slug,
            defaults={
                'name': f'Набор ТО — 2 позиции EXEED {model_name} 2.0T SQRF4J20C',
                'brand': brand, 'car_model': model,
                'engine': '2.0T SQRF4J20C', 'year_from': year, 'year_to': None,
                'description': (
                    f'Набор ТО — 2 позиции для EXEED {model_name} 2.0T '
                    f'SQRF4J20C ({body}, '
                    f'с {"августа 2022" if model_name == "TXL" else "апреля 2020"}): '
                    'воздушный фильтр '
                    'двигателя и салонный фильтр. Масляный фильтр в набор '
                    'не входит. '
                ) + ('Не для TXL 1.6T. ' if model_name == 'TXL' else '') + (
                    'Перед заказом сверьте двигатель и артикулы '
                    'с VIN.'
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
    dependencies = [('catalog', '0051_chery_m11_maintenance_kit')]
    operations = [migrations.RunPython(add_exeed_20_kits, migrations.RunPython.noop)]
