from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0090_confirm_new_parts_by_exact_catalog_contacts'),
    ]

    operations = [
        migrations.AddField(
            model_name='seller',
            name='address',
            field=models.CharField(blank=True, default='', max_length=500, verbose_name='Адрес'),
        ),
        migrations.AddField(
            model_name='seller',
            name='latitude',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True, verbose_name='Широта'),
        ),
        migrations.AddField(
            model_name='seller',
            name='longitude',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True, verbose_name='Долгота'),
        ),
        migrations.AddField(
            model_name='seller',
            name='website',
            field=models.URLField(blank=True, default='', max_length=500, verbose_name='Сайт организации'),
        ),
        migrations.CreateModel(
            name='SellerExtraPhone',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('phone', models.CharField(max_length=20, verbose_name='Телефон')),
                ('seller', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='extra_phones', to='core.seller', verbose_name='Продавец')),
            ],
            options={
                'verbose_name': 'Дополнительный телефон',
                'verbose_name_plural': 'Дополнительные телефоны',
                'ordering': ['id'],
            },
        ),
        migrations.CreateModel(
            name='SellerWorkInterval',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('weekday', models.PositiveSmallIntegerField(verbose_name='День недели')),
                ('opens', models.TimeField(blank=True, null=True, verbose_name='Открытие')),
                ('closes', models.TimeField(blank=True, null=True, verbose_name='Закрытие')),
                ('is_closed', models.BooleanField(default=False, verbose_name='Выходной')),
                ('position', models.PositiveSmallIntegerField(default=0, verbose_name='Порядок')),
                ('seller', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='work_hours', to='core.seller', verbose_name='Продавец')),
            ],
            options={
                'verbose_name': 'Интервал работы',
                'verbose_name_plural': 'Режим работы',
                'ordering': ['weekday', 'position', 'id'],
            },
        ),
        migrations.CreateModel(
            name='SellerSocialLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('whatsapp', 'WhatsApp'), ('instagram', 'Instagram'), ('telegram', 'Telegram'), ('tiktok', 'TikTok'), ('vk', 'VK'), ('youtube', 'YouTube')], max_length=20, verbose_name='Площадка')),
                ('url', models.URLField(max_length=500, verbose_name='Ссылка')),
                ('seller', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='social_links', to='core.seller', verbose_name='Продавец')),
            ],
            options={
                'verbose_name': 'Ссылка на соцсеть',
                'verbose_name_plural': 'Ссылки на соцсети',
                'ordering': ['kind', 'id'],
            },
        ),
        migrations.CreateModel(
            name='SellerExternalCard',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source', models.CharField(choices=[('two_gis', '2ГИС'), ('yandex', 'Яндекс Карты'), ('google', 'Google Карты')], max_length=20, verbose_name='Источник')),
                ('external_id', models.CharField(max_length=64, verbose_name='Внешний ID')),
                ('card_url', models.URLField(max_length=500, verbose_name='Ссылка на карточку')),
                ('exported_at', models.DateTimeField(blank=True, null=True, verbose_name='Дата выгрузки')),
                ('seller', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='external_cards', to='core.seller', verbose_name='Продавец')),
            ],
            options={
                'verbose_name': 'Исходная карточка',
                'verbose_name_plural': 'Исходные карточки',
                'ordering': ['source', 'id'],
            },
        ),
        migrations.AddConstraint(
            model_name='sellerextraphone',
            constraint=models.UniqueConstraint(fields=('seller', 'phone'), name='uniq_seller_extra_phone'),
        ),
        migrations.AddConstraint(
            model_name='sellerworkinterval',
            constraint=models.UniqueConstraint(fields=('seller', 'weekday', 'position'), name='uniq_seller_work_interval'),
        ),
        migrations.AddConstraint(
            model_name='sellersociallink',
            constraint=models.UniqueConstraint(fields=('seller', 'kind', 'url'), name='uniq_seller_social_link'),
        ),
        migrations.AddConstraint(
            model_name='sellerexternalcard',
            constraint=models.UniqueConstraint(fields=('source', 'external_id'), name='uniq_seller_external_card'),
        ),
    ]
