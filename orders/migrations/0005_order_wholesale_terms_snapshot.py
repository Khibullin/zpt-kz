from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0004_order_type_utm'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='wholesale_terms_snapshot',
            field=models.JSONField(
                blank=True,
                default=dict,
                verbose_name='Снимок оптовых условий',
            ),
        ),
    ]
