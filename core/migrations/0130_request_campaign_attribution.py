from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0129_buyer_result_channels')]
    operations = [
        migrations.AddField(
            model_name='request', name='campaign_attribution',
            field=models.JSONField(
                default=dict, blank=True, verbose_name='Источник рекламы',
                help_text='Метки кампании при создании заявки; не подтверждают покупку.',
            ),
        ),
    ]
