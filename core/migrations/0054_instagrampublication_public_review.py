from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0053_instagrampublication_feed_placement'),
    ]

    operations = [
        migrations.AddField(
            model_name='instagrampublication',
            name='approved_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Одобрено'),
        ),
        migrations.AddField(
            model_name='instagrampublication',
            name='public_payload',
            field=models.JSONField(blank=True, default=dict, verbose_name='Публичное представление'),
        ),
        migrations.AddField(
            model_name='instagrampublication',
            name='review_detail',
            field=models.TextField(blank=True, default='', verbose_name='Пояснение проверки'),
        ),
        migrations.AddField(
            model_name='instagrampublication',
            name='review_reason',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='Причина проверки'),
        ),
    ]
