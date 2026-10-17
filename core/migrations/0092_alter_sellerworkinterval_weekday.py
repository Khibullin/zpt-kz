"""Keep the existing weekday range validators in Django migration state.

The existing Cursor card-enrichment migration introduced SellerWorkInterval
without serializing weekday validators. No seller rows or sending rules change.
"""
import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0091_seller_card_enrichment'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sellerworkinterval',
            name='weekday',
            field=models.PositiveSmallIntegerField(
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(6),
                ],
                verbose_name='День недели',
            ),
        ),
    ]
