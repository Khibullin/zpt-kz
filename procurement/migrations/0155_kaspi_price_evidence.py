from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('procurement', '0154_phaeton_pilot_price_observations')]
    operations = [migrations.AddField(
        model_name='kaspipriceaudit', name='price_evidence',
        field=models.JSONField(default=dict, blank=True))]
