from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0020_product_catalog_foundation'),
    ]

    operations = [
        migrations.AddField(
            model_name='sellerprofile',
            name='wholesale_enabled',
            field=models.BooleanField(
                default=False,
                help_text='Включает публичную постоянную оптовую витрину продавца.',
                verbose_name='Оптовая витрина',
            ),
        ),
        migrations.AddField(
            model_name='sellerprofile',
            name='wholesale_min_order_qty',
            field=models.PositiveIntegerField(
                default=10,
                help_text='Минимальная сумма количества всех оптовых товаров в одном заказе.',
                validators=[MinValueValidator(1)],
                verbose_name='Минимум опта, шт.',
            ),
        ),
    ]
