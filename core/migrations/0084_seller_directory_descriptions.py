from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0083_seller_directory_photo')]

    operations = [
        migrations.AddField(
            model_name='seller', name='directory_description',
            field=models.TextField(blank=True, verbose_name='Описание для каталога (русский)',
                help_text='Публичное описание компании. Внутренние заметки сюда не переносите.'),
        ),
        migrations.AddField(
            model_name='seller', name='directory_description_kk',
            field=models.TextField(blank=True, verbose_name='Описание для каталога (казахский)'),
        ),
    ]
