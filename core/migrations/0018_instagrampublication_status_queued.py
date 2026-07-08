from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0017_instagrampublication_publishing_started_at'),
    ]

    operations = [
        migrations.AlterField(
            model_name='instagrampublication',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft', 'Черновик'),
                    ('approved', 'Одобрено'),
                    ('queued', 'В очереди'),
                    ('publishing', 'Публикуется'),
                    ('published', 'Опубликовано'),
                    ('failed', 'Ошибка'),
                    ('cancelled', 'Отменено'),
                ],
                default='draft',
                max_length=20,
                verbose_name='Статус',
            ),
        ),
    ]
