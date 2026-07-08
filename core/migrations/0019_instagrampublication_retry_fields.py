from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0018_instagrampublication_status_queued'),
    ]

    operations = [
        migrations.AddField(
            model_name='instagrampublication',
            name='last_attempt_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Последняя попытка'),
        ),
        migrations.AddField(
            model_name='instagrampublication',
            name='retry_count',
            field=models.PositiveSmallIntegerField(default=0, verbose_name='Число попыток публикации'),
        ),
    ]
