from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0036_maintenance_kit_cover'),
    ]

    operations = [
        migrations.CreateModel(
            name='MaintenanceKitCarRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('brand', models.CharField(max_length=100, verbose_name='Марка')),
                ('model', models.CharField(max_length=100, verbose_name='Модель')),
                ('year', models.PositiveSmallIntegerField(verbose_name='Год')),
                ('engine', models.CharField(max_length=80, verbose_name='Двигатель')),
                ('vin', models.CharField(
                    blank=True,
                    default='',
                    help_text='Необязательно. Помогает уточнить модификацию.',
                    max_length=32,
                    verbose_name='VIN',
                )),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
            ],
            options={
                'verbose_name': 'Запрос комплекта ТО',
                'verbose_name_plural': 'Запросы комплектов ТО',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='maintenancekitcarrequest',
            index=models.Index(fields=['brand', 'model'], name='kit_car_req_brand_model_idx'),
        ),
    ]
