from django.core.management.base import BaseCommand
from catalog.models import Country, Brand, CarModel, Category


class Command(BaseCommand):
    help = 'Заполнение расширенных справочников (страны, марки, модели, категории)'

    def handle(self, *args, **kwargs):
        self.stdout.write('Очистка старых справочников...')

        CarModel.objects.all().delete()
        Brand.objects.all().delete()
        Country.objects.all().delete()
        Category.objects.all().delete()

        data = {
            'Япония': {
                'Toyota': ['Camry', 'Land Cruiser', 'Corolla', 'RAV4', 'Prado'],
                'Lexus': ['ES', 'RX', 'GX', 'LX'],
                'Nissan': ['X-Trail', 'Patrol', 'Teana', 'Qashqai'],
                'Honda': ['CR-V', 'Accord', 'Civic'],
                'Mazda': ['CX-5', 'CX-9', '6'],
                'Subaru': ['Forester', 'Outback', 'XV'],
                'Mitsubishi': ['Outlander', 'Pajero', 'L200'],
                'Suzuki': ['Grand Vitara', 'SX4'],
            },
            'Корея': {
                'Hyundai': ['Tucson', 'Elantra', 'Santa Fe', 'Accent'],
                'Kia': ['Sportage', 'Cerato', 'Sorento', 'Rio'],
                'Genesis': ['G70', 'G80', 'GV80'],
                'SsangYong': ['Rexton', 'Actyon', 'Kyron'],
            },
            'Германия': {
                'BMW': ['X5', 'X6', '3 Series', '5 Series'],
                'Mercedes': ['E-Class', 'C-Class', 'S-Class', 'GLE'],
                'Audi': ['A4', 'A6', 'Q5', 'Q7'],
                'Volkswagen': ['Passat', 'Tiguan', 'Polo'],
                'Skoda': ['Octavia', 'Superb', 'Kodiaq'],
                'Porsche': ['Cayenne', 'Macan', 'Panamera'],
            },
            'США': {
                'Chevrolet': ['Cruze', 'Captiva', 'Malibu', 'Tahoe'],
                'Ford': ['Focus', 'Explorer', 'F-150'],
                'Jeep': ['Grand Cherokee', 'Wrangler'],
            },
            'Китай': {
                'Geely': ['Coolray', 'Atlas', 'Emgrand'],
                'Chery': ['Tiggo 4', 'Tiggo 7', 'Tiggo 8'],
                'Haval': ['H6', 'F7', 'Jolion'],
                'JAC': ['S3', 'S5'],
                'Exeed': ['TXL', 'VX'],
                'Tank': ['300', '500'],
            },
            'Россия': {
                'Lada': ['Vesta', 'Granta', 'Niva'],
                'UAZ': ['Patriot', 'Hunter'],
                'GAZ': ['Gazelle'],
            },
            'Великобритания': {
                'Land Rover': ['Range Rover', 'Discovery', 'Defender'],
            },
            'Франция': {
                'Renault': ['Duster', 'Logan', 'Megane'],
            },
        }

        categories_data = [
            'Двигатель',
            'Подвеска',
            'Кузов',
            'Тормоза',
            'Электрика',
            'Трансмиссия',
            'Оптика',
            'Салон',
            'Охлаждение',
            'Фильтры',
        ]

        countries_created = 0
        brands_created = 0
        models_created = 0
        categories_created = 0

        for country_name, brands in data.items():
            country = Country.objects.create(name=country_name)
            countries_created += 1

            for brand_name, model_names in brands.items():
                brand = Brand.objects.create(
                    country=country,
                    name=brand_name
                )
                brands_created += 1

                for model_name in model_names:
                    CarModel.objects.create(
                        brand=brand,
                        name=model_name,
                    )
                    models_created += 1

        for category_name in categories_data:
            Category.objects.create(name=category_name)
            categories_created += 1

        self.stdout.write(self.style.SUCCESS(
            f'Готово: стран={countries_created}, марок={brands_created}, моделей={models_created}, категорий={categories_created}'
        ))