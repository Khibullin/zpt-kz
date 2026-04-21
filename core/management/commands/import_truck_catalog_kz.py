from django.core.management.base import BaseCommand
from core.models import Country, Brand, CarModel


TRUCK_CATALOG = {
    'Россия': {
        'КамАЗ': ['4310', '5320', '55111', '6520'],
        'МАЗ': ['4370', '5336', '5440', '5516'],
        'ГАЗ': ['3307', '3309', 'Газель Next', 'Соболь'],
        'Урал': ['4320', '5557', '6370'],
    },
    'Китай': {
        'HOWO': ['A7', 'T5G', 'TX'],
        'Shacman': ['F2000', 'F3000', 'X3000'],
        'FAW': ['J6', 'CA3250', 'CA3310'],
    },
    'Япония': {
        'Isuzu': ['NQR', 'FVR', 'ELF'],
        'Hino': ['300', '500', '700'],
    },
    'Европа': {
        'MAN': ['TGA', 'TGS', 'TGX'],
        'Mercedes-Benz Trucks': ['Actros', 'Atego', 'Axor'],
        'Volvo': ['FH', 'FM', 'FL'],
        'Scania': ['P-series', 'G-series', 'R-series'],
    },
}


class Command(BaseCommand):
    help = 'Импорт грузового справочника: страны, марки и модели'

    def handle(self, *args, **options):
        created_countries = 0
        created_brands = 0
        created_models = 0

        for country_name, brands in TRUCK_CATALOG.items():
            country, country_created = Country.objects.get_or_create(name=country_name)
            if country_created:
                created_countries += 1
                self.stdout.write(self.style.SUCCESS(f'Страна создана: {country_name}'))
            else:
                self.stdout.write(f'Страна уже есть: {country_name}')

            for brand_name, models in brands.items():
                brand, brand_created = Brand.objects.get_or_create(
                    country=country,
                    name=brand_name,
                    transport_type='truck',
                )
                if brand_created:
                    created_brands += 1
                    self.stdout.write(self.style.SUCCESS(f'  Марка создана: {brand_name}'))
                else:
                    self.stdout.write(f'  Марка уже есть: {brand_name}')

                for model_name in models:
                    car_model, model_created = CarModel.objects.get_or_create(
                        brand=brand,
                        name=model_name,
                        transport_type='truck',
                    )
                    if model_created:
                        created_models += 1
                        self.stdout.write(self.style.SUCCESS(f'    Модель создана: {model_name}'))
                    else:
                        self.stdout.write(f'    Модель уже есть: {model_name}')

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Импорт грузового справочника завершён'))
        self.stdout.write(
            self.style.SUCCESS(
                f'Создано: стран={created_countries}, марок={created_brands}, моделей={created_models}'
            )
        )