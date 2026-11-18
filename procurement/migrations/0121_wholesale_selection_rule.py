from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from decimal import Decimal


class Migration(migrations.Migration):
    dependencies = [
        ('procurement', '0120_import_omega_firsthand_20261117'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='WholesaleSelectionRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('singleton_key', models.PositiveSmallIntegerField(default=1, unique=True)),
                ('min_cost', models.DecimalField(decimal_places=2, default=Decimal('2000'), max_digits=14)),
                ('max_vehicle_age', models.PositiveSmallIntegerField(default=20)),
                ('category_groups', models.JSONField(blank=True, default=list)),
                ('in_stock_only', models.BooleanField(default=True)),
                ('classification_mode', models.CharField(default='auto', max_length=12)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('supplier', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='procurement.supplier')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
