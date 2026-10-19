from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('core', '0119_import_atyrau_parts_316_20261018')]
    operations = [
        migrations.AddField(model_name='seller', name='request_filters', field=models.JSONField(null=True, blank=True, default=None, verbose_name='Подтверждённый фильтр заявок')),
        migrations.AddField(model_name='seller', name='request_filters_updated_at', field=models.DateTimeField(null=True, blank=True, verbose_name='Фильтр обновлён')),
    ]
