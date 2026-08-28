from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0003_manual_checkout_seller_snapshot'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='order_type',
            field=models.CharField(
                choices=[('retail', 'Розничный'), ('wholesale', 'Оптовый')],
                db_index=True,
                default='retail',
                max_length=20,
                verbose_name='Тип заказа',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='utm_source',
            field=models.CharField(
                blank=True,
                default='',
                max_length=100,
                verbose_name='UTM source',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='utm_medium',
            field=models.CharField(
                blank=True,
                default='',
                max_length=100,
                verbose_name='UTM medium',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='utm_campaign',
            field=models.CharField(
                blank=True,
                default='',
                max_length=150,
                verbose_name='UTM campaign',
            ),
        ),
    ]
