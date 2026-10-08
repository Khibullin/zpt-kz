from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0007_order_seller_profile'),
    ]

    operations = [
        migrations.AlterField(
            model_name='wholesalefunnelevent',
            name='event_type',
            field=models.CharField(
                choices=[
                    ('storefront_view', 'Витрина'),
                    ('product_view', 'Карточка товара'),
                    ('price_download', 'Скачивание прайса'),
                    ('add_to_cart', 'Добавление в корзину'),
                    ('checkout_view', 'Оформление'),
                    ('order_created', 'Заказ'),
                    ('seller_whatsapp_click', 'WhatsApp продавцу'),
                ],
                db_index=True,
                max_length=32,
                verbose_name='Тип события',
            ),
        ),
    ]
