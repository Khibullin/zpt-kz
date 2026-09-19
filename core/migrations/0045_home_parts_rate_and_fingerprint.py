from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0044_request_home_short_form'),
    ]

    operations = [
        migrations.AddField(
            model_name='request',
            name='idempotency_fingerprint',
            field=models.CharField(
                blank=True,
                default='',
                max_length=64,
                verbose_name='Отпечаток содержимого заявки',
            ),
        ),
        migrations.CreateModel(
            name='HomePartsRateBucket',
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
                ('key', models.CharField(max_length=80, unique=True)),
                ('hits', models.PositiveIntegerField(default=0)),
                ('window_started_at', models.DateTimeField()),
            ],
            options={
                'verbose_name': 'Лимит короткой формы',
                'verbose_name_plural': 'Лимиты короткой формы',
            },
        ),
    ]
