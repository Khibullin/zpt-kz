from django.core.management.base import BaseCommand
from catalog.models import Product, ProductImage


class Command(BaseCommand):
    help = "Удаляет все товары и все дополнительные фото товаров из каталога"

    def handle(self, *args, **options):
        product_images_count = ProductImage.objects.count()
        products_count = Product.objects.count()

        self.stdout.write(f"Фото товаров найдено: {product_images_count}")
        self.stdout.write(f"Товаров найдено: {products_count}")

        ProductImage.objects.all().delete()
        Product.objects.all().delete()

        self.stdout.write(self.style.SUCCESS("Очистка каталога завершена"))
        self.stdout.write(self.style.SUCCESS(f"Удалено фото товаров: {product_images_count}"))
        self.stdout.write(self.style.SUCCESS(f"Удалено товаров: {products_count}"))