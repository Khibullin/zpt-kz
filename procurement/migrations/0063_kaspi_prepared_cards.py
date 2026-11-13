from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def seed_purchased_cards(apps, schema_editor):
    from procurement.kaspi_materials import process_purchased_cards
    process_purchased_cards(fetch_photos=False)


class Migration(migrations.Migration):
    dependencies = [
        ('procurement', '0062_filter_dictionary_indexes'),
        ('inventory', '0016_importpreview_previewfile'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(name='KaspiPreparedCard', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('identity', models.CharField(max_length=64, unique=True)),
            ('brand', models.CharField(max_length=120)),
            ('article', models.CharField(max_length=120)),
            ('title', models.CharField(max_length=500)),
            ('description', models.TextField(blank=True)),
            ('photo_url', models.URLField(blank=True, max_length=2048)),
            ('category', models.CharField(blank=True, max_length=512)),
            ('characteristics', models.JSONField(blank=True, default=dict)),
            ('applicability', models.TextField(blank=True)),
            ('applicability_source', models.URLField(blank=True, max_length=2048)),
            ('verified', models.BooleanField(default=False)),
            ('manual_fields', models.JSONField(blank=True, default=list)),
            ('source', models.JSONField(blank=True, default=dict)),
            ('revision', models.PositiveIntegerField(default=1)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('offer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='procurement.offer')),
        ]),
        migrations.CreateModel(name='KaspiCardImage', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('digest', models.CharField(max_length=64)),
            ('data', models.BinaryField()),
            ('thumbnail', models.BinaryField()),
            ('width', models.PositiveIntegerField()),
            ('height', models.PositiveIntegerField()),
            ('source_url', models.URLField(blank=True, max_length=2048)),
            ('position', models.PositiveIntegerField(default=0)),
            ('active', models.BooleanField(default=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('card', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='images', to='procurement.kaspipreparedcard')),
        ], options={'ordering': ['position', 'pk'], 'unique_together': {('card', 'digest')}}),
        migrations.CreateModel(name='KaspiCardPrice', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('mode', models.CharField(choices=[('algorithm', 'По алгоритму'), ('manual', 'Вручную')], default='algorithm', max_length=12)),
            ('amount', models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
            ('revision', models.PositiveIntegerField(default=1)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('card', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='procurement.kaspipreparedcard')),
            ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ], options={'unique_together': {('card', 'user')}}),
        migrations.CreateModel(name='KaspiPricingRule', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('enabled', models.BooleanField(default=False)),
            ('method', models.CharField(choices=[('minimum_minus_amount', 'Ниже минимальной цены на сумму'), ('minimum_minus_percent', 'Ниже минимальной цены на процент'), ('cost_plus_percent', 'Наценка к закупочной цене')], default='minimum_minus_amount', max_length=24)),
            ('value', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
            ('min_profit', models.DecimalField(decimal_places=2, default=1000, max_digits=12)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.RunPython(seed_purchased_cards, migrations.RunPython.noop),
    ]
