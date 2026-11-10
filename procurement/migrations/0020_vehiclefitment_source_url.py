from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('procurement', '0019_supplier_stock_retail')]

    operations = [
        migrations.AlterField(
            model_name='vehiclefitment',
            name='source_url',
            field=models.URLField('Источник применимости', max_length=2048),
        ),
    ]
