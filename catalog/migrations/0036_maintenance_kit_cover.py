from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0035_maintenance_kit'),
    ]

    operations = [
        migrations.AddField(
            model_name='maintenancekit',
            name='cover',
            field=models.ImageField(
                blank=True,
                help_text=(
                    'Одна общая фотография набора. Показывается в списке и в шапке '
                    'страницы состава. Не заменяет фотографии товаров состава.'
                ),
                null=True,
                upload_to='maintenance_kits/',
                verbose_name='Общее фото комплекта',
            ),
        ),
    ]
