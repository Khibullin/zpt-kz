from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0045_home_parts_rate_and_fingerprint'),
    ]

    operations = [
        migrations.CreateModel(
            name='GuideFaqVote',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'faq_id',
                    models.CharField(
                        db_index=True,
                        max_length=32,
                        verbose_name='ID вопроса',
                    ),
                ),
                (
                    'session_key',
                    models.CharField(
                        db_index=True,
                        max_length=40,
                        verbose_name='Сессия',
                    ),
                ),
                (
                    'helpful',
                    models.BooleanField(verbose_name='Полезно'),
                ),
                (
                    'created_at',
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name='Создано',
                    ),
                ),
                (
                    'updated_at',
                    models.DateTimeField(
                        auto_now=True,
                        verbose_name='Обновлено',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Оценка ответа ZPT Гида',
                'verbose_name_plural': 'Оценки ответов ZPT Гида',
            },
        ),
        migrations.AddConstraint(
            model_name='guidefaqvote',
            constraint=models.UniqueConstraint(
                fields=('faq_id', 'session_key'),
                name='core_guidefaqvote_faq_session_uniq',
            ),
        ),
    ]
