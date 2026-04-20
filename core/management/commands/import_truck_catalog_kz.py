from django.core.management.base import BaseCommand
from core.models import Country, Brand, CarModel


TRUCK_CATALOG = {
    "Россия": {
        "КамАЗ": ["43118", "5320", "65115", "6520", "5490"],
        "ГАЗ": ["Газель", "Газон Next", "Садко Next", "Валдай"],
        "Урал": ["4320", "5557", "6370"],
    },
    "Беларусь": {
        "МАЗ": ["4370", "5440", "5516", "6312"],
    },
    "Германия": {
        "MAN": ["TGA", "TGS", "TGX", "L2000"],
        "Mercedes-Benz Trucks": ["Actros", "Atego", "Axor", "Unimog"],
    },
    "Швеция": {
        "Volvo Trucks": ["FH", "FM", "FL", "FE"],
        "Scania": ["P-series", "G-series", "R-series", "S-series"],
    },
    "Нидерланды": {
        "DAF": ["CF", "XF", "LF"],
    },
    "Италия": {
        "Iveco": ["Daily", "EuroCargo", "Stralis", "Trakker"],
    },
    "Китай": {
        "HOWO": ["A7", "T5G", "TX"],
        "Shacman": ["F2000", "F3000", "X3000"],
        "FAW": ["J6", "Tiger", "CA"],
        "Dongfeng": ["KR", "KL", "Captain"],
        "Foton": ["Auman", "Ollin", "Tunland"],
    },
    "Япония": {
        "Isuzu": ["NQR", "FVR", "GIGA", "ELF"],
        "Hino": ["300", "500", "700"],
    },
}


class Command(BaseCommand):
    help = "Очищает временные truck-дубли и импортирует реальный грузовой справочник"

    def handle(self, *args, **options):
        # Удаляем только truck-данные, car не трогаем
        truck_models_deleted, _ = CarModel.objects.filter(transport_type="truck").delete()
        truck_brands_deleted, _ = Brand.objects.filter(transport_type="truck").delete()

        created_countries = 0
        created_brands = 0
        created_models = 0

        for country_name, brands in TRUCK_CATALOG.items():
            country, country_created = Country.objects.get_or_create(name=country_name)
            if country_created:
                created_countries += 1

            for brand_name, models in brands.items():
                brand, brand_created = Brand.objects.get_or_create(
                    country=country,
                    name=brand_name,
                    transport_type="truck",
                )
                if brand_created:
                    created_brands += 1

                for model_name in models:
                    car_model, model_created = CarModel.objects.get_or_create(
                        brand=brand,
                        name=model_name,
                        transport_type="truck",
                    )
                    if model_created:
                        created_models += 1

        self.stdout.write(self.style.SUCCESS(
            f"Готово. Удалено truck-моделей: {truck_models_deleted}, "
            f"удалено truck-марок: {truck_brands_deleted}, "
            f"добавлено стран: +{created_countries}, марок: +{created_brands}, моделей: +{created_models}"
        ))