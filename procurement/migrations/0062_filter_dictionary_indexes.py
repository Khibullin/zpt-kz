from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('procurement', '0061_reviewed_new12d')]

    operations = [
        migrations.AddIndex(
            model_name='offer',
            index=models.Index(fields=['supplier', 'category'], name='proc_offer_supplier_category'),
        ),
        migrations.AddIndex(
            model_name='vehiclefitment',
            index=models.Index(fields=['verified', 'make', 'model', 'offer'], name='proc_fitment_filter_options'),
        ),
    ]
