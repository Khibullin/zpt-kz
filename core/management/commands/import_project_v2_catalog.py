from pathlib import Path
import ast

from django.core.management.base import BaseCommand, CommandError

from core.models import Country, Brand, CarModel


class Command(BaseCommand):
    help = "Импортирует страны, марки и модели из project-v2 seed_marketparts.py"

    def handle(self, *args, **options):
        source_file = Path(r"C:\projects\project-v2\market-parts\core\management\commands\seed_marketparts.py")

        if not source_file.exists():
            raise CommandError(f"Файл не найден: {source_file}")

        text = source_file.read_text(encoding="utf-8")
        module = ast.parse(text)

        countries_data = None
        brands_models = None

        for node in ast.walk(module):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if target.id == "countries_data":
                            countries_data = ast.literal_eval(node.value)
                        elif target.id == "brands_models":
                            brands_models = ast.literal_eval(node.value)

        if not countries_data:
            raise CommandError("Не найден countries_data в seed_marketparts.py")

        if not brands_models:
            raise CommandError("Не найден brands_models в seed_marketparts.py")

        self.stdout.write("Импорт стран...")
        countries_map = {}

        for index, item in enumerate(countries_data, start=1):
            name = item[0]
            country, _ = Country.objects.update_or_create(
                name=name,
            )
            countries_map[item[1]] = country

        self.stdout.write("Импорт марок и моделей...")

        created_brands = 0
        created_models = 0

        for item in brands_models:
            country_slug = item["country"]
            brand_name = item["name"]
            models_list = item.get("models", [])

            country = countries_map.get(country_slug)
            if not country:
                continue

            brand, brand_created = Brand.objects.get_or_create(
                country=country,
                name=brand_name,
            )
            if brand_created:
                created_brands += 1

            for model_item in models_list:
                model_name = model_item[0]

                car_model, model_created = CarModel.objects.get_or_create(
                    brand=brand,
                    name=model_name,
                )
                if model_created:
                    created_models += 1

        self.stdout.write(self.style.SUCCESS(
            f"Готово. Марок создано: {created_brands}, моделей создано: {created_models}"
        ))