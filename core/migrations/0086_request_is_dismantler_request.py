from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0085_seller_catalog_only_sellerlead_service_seller'),
    ]

    operations = [
        migrations.AddField(
            model_name='request',
            name='is_dismantler_request',
            field=models.BooleanField(
                default=False,
                db_index=True,
                verbose_name='Заявка только авторазборам',
                help_text='Из раздела авторазборов; получатели — только подходящие авторазборы.',
            ),
        ),
    ]
