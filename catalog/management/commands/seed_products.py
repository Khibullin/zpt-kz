from django.core.management.base import BaseCommand
from catalog.models import Brand, CarModel, Category, Product


class Command(BaseCommand):
    help = 'Заполнение тестовыми товарами (много товаров)'

    def handle(self, *args, **kwargs):
        Product.objects.all().delete()

        categories = list(Category.objects.all())
        models = list(CarModel.objects.select_related('brand'))

        sellers = [
            ('AG Parts', '77771234567'),
            ('Korea Parts', '77775554433'),
            ('Japan Parts', '77779998877'),
            ('Euro Parts', '77770001122'),
        ]

        articles = 1000
        count = 0

        for model in models:
            brand = model.brand

            for category in categories:
                for i in range(2):  # по 2 товара на комбинацию
                    seller_name, phone = sellers[count % len(sellers)]

                    Product.objects.create(
                        title=f'{category.name} для {brand.name} {model.name}',
                        article=f'ART-{articles}',
                        price=5000 + (count * 700),
                        condition='new' if count % 2 == 0 else 'used',
                        brand=brand,
                        car_model=model,
                        category=category,
                        seller_name=seller_name,
                        whatsapp_number=phone,
                        compatibility=f'{brand.name} {model.name}',
                        description=f'{category.name} для автомобиля {brand.name} {model.name}',
                        is_published=True,
                    )

                    articles += 1
                    count += 1

        self.stdout.write(self.style.SUCCESS(f'Создано товаров: {count}'))