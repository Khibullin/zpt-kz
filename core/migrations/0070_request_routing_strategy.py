from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0069_normalize_platform_cities'),
    ]

    operations = [
        migrations.AddField(
            model_name='request',
            name='routing_strategy',
            field=models.CharField(
                blank=True,
                choices=[
                    ('matched_city', 'Подобрано в городе'),
                    ('matched_custom', 'Подобрано в выбранных городах'),
                    ('matched_kazakhstan', 'Подобрано по Казахстану'),
                    ('fallback_kazakhstan', 'Fallback по Казахстану'),
                    ('all_kz', 'Все допущенные продавцы Казахстана'),
                    ('no_match', 'Продавцы не найдены'),
                ],
                db_index=True,
                default='',
                max_length=32,
                verbose_name='Фактический маршрут подбора',
            ),
        ),
    ]
