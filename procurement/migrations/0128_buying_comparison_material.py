from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('procurement', '0127_kaspi_price_audit')]

    operations = [
        migrations.CreateModel(
            name='BuyingComparisonMaterial',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_filename', models.CharField(blank=True, max_length=255)),
                ('kaspi_product_id', models.CharField(blank=True, max_length=80)),
                ('supplier_url', models.URLField(blank=True, max_length=2048)),
                ('kaspi_url', models.URLField(blank=True, max_length=2048)),
                ('supplier_data', models.JSONField(blank=True, default=dict)),
                ('kaspi_data', models.JSONField(blank=True, default=dict)),
                ('supplier_status', models.CharField(default='pending', max_length=24)),
                ('kaspi_status', models.CharField(default='pending', max_length=24)),
                ('supplier_error', models.CharField(blank=True, max_length=250)),
                ('kaspi_error', models.CharField(blank=True, max_length=250)),
                ('checked_at', models.DateTimeField(auto_now=True)),
                ('offer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='comparison_materials', to='procurement.offer')),
            ],
            options={
                'indexes': [models.Index(fields=['offer', 'source_filename'], name='proc_cmp_offer_source')],
                'constraints': [models.UniqueConstraint(fields=('offer', 'source_filename', 'kaspi_product_id'), name='proc_comparison_offer_export_card')],
            },
        ),
    ]
