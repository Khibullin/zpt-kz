from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0082_seller_dismantler_directory')]

    operations = [
        migrations.AddField(
            model_name='seller', name='directory_photo',
            field=models.ImageField(blank=True, upload_to='seller_directory/',
                verbose_name='Фото компании для каталога',
                help_text='Реальная фотография вашего авторазбора или склада.'),
        ),
    ]
