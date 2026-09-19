from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0043_seller_request_page_consent_lock'),
    ]

    operations = [
        migrations.AlterField(
            model_name='request',
            name='transport_type',
            field=models.CharField(
                blank=True,
                choices=[('car', 'Легковые'), ('truck', 'Грузовые')],
                default='',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='request',
            name='year',
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                verbose_name='Год выпуска',
            ),
        ),
        migrations.AddField(
            model_name='request',
            name='vin',
            field=models.CharField(
                blank=True,
                default='',
                max_length=32,
                verbose_name='VIN',
            ),
        ),
        migrations.AddField(
            model_name='request',
            name='source',
            field=models.CharField(
                choices=[
                    ('classic', 'Классическая заявка'),
                    ('home_short', 'Короткая форма главной'),
                ],
                db_index=True,
                default='classic',
                max_length=20,
                verbose_name='Источник заявки',
            ),
        ),
        migrations.AddField(
            model_name='request',
            name='dispatch_mode',
            field=models.CharField(
                choices=[
                    ('matched', 'Подбор по специализации'),
                    ('all_kz', 'Все допущенные продавцы Казахстана'),
                ],
                db_index=True,
                default='matched',
                max_length=20,
                verbose_name='Режим рассылки',
            ),
        ),
        migrations.AddField(
            model_name='request',
            name='idempotency_key',
            field=models.CharField(
                blank=True,
                default='',
                max_length=64,
                verbose_name='Ключ идемпотентности',
            ),
        ),
        migrations.AddConstraint(
            model_name='request',
            constraint=models.UniqueConstraint(
                condition=~models.Q(idempotency_key=''),
                fields=('idempotency_key',),
                name='uniq_request_idempotency_key',
            ),
        ),
    ]
