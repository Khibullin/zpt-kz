from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('procurement', '0126_permanent_buying_register')]

    operations = [
        migrations.CreateModel(
            name='KaspiPriceAudit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_filename', models.CharField(max_length=255)),
                ('min_price', models.DecimalField(decimal_places=2, max_digits=14)),
                ('product_id', models.CharField(blank=True, max_length=80)),
                ('card_url', models.URLField(blank=True, max_length=2048)),
                ('card_title', models.CharField(blank=True, max_length=500)),
                ('seller_name', models.CharField(blank=True, max_length=180)),
                ('cost_snapshot', models.DecimalField(decimal_places=2, max_digits=14)),
                ('commission_snapshot', models.DecimalField(decimal_places=2, max_digits=6)),
                ('expenses_snapshot', models.DecimalField(decimal_places=2, max_digits=14)),
                ('profit', models.DecimalField(decimal_places=2, max_digits=18)),
                ('roi', models.DecimalField(decimal_places=4, max_digits=18)),
                ('card_ids', models.JSONField(blank=True, default=list)),
                ('identity_status', models.CharField(default='needs_review', max_length=24)),
                ('observed_at', models.DateTimeField(auto_now=True)),
                ('offer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                           related_name='kaspi_price_audits', to='procurement.offer')),
            ],
        ),
        migrations.AddConstraint(
            model_name='kaspipriceaudit',
            constraint=models.UniqueConstraint(fields=('offer', 'source_filename'),
                                                name='proc_kaspi_audit_offer_file'),
        ),
        migrations.AddIndex(
            model_name='kaspipriceaudit',
            index=models.Index(fields=['offer', '-observed_at'],
                               name='proc_audit_offer_latest'),
        ),
    ]
