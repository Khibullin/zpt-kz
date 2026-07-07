from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0016_instagrampublication'),
    ]

    operations = [
        migrations.AddField(
            model_name='instagrampublication',
            name='publishing_started_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name='Начало публикации',
            ),
        ),
    ]
