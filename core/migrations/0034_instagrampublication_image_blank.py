from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0033_buyercontact_is_control_recipient'),
    ]

    operations = [
        migrations.AlterField(
            model_name='instagrampublication',
            name='image',
            field=models.ImageField(
                blank=True,
                upload_to='instagram_stories/',
                verbose_name='Карточка Story',
            ),
        ),
    ]
