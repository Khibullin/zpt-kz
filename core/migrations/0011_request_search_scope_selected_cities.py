from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_whatsappmessagelog'),
    ]

    operations = [
        migrations.AddField(
            model_name='request',
            name='search_scope',
            field=models.CharField(
                choices=[
                    ('city', 'Только мой город'),
                    ('kazakhstan', 'Весь Казахстан'),
                    ('custom', 'Выбрать города'),
                ],
                default='city',
                max_length=20,
            ),
        ),

        migrations.AddField(
            model_name='request',
            name='selected_cities',
            field=models.TextField(
                blank=True,
                default='',
            ),
        ),
    ]