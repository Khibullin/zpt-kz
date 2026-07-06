from django.db import migrations, models

import catalog.models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0014_product_price_on_request_alter_product_price'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sellerprofile',
            name='delivery_info',
            field=models.TextField(
                blank=True,
                default=catalog.models.DEFAULT_SELLER_DELIVERY_INFO,
                verbose_name='Доставка и оплата',
            ),
        ),
        migrations.AlterField(
            model_name='sellerprofile',
            name='work_hours',
            field=models.CharField(
                blank=True,
                default=catalog.models.DEFAULT_SELLER_WORK_HOURS,
                max_length=255,
                verbose_name='График работы',
            ),
        ),
    ]
