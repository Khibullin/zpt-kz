from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('procurement', '0121_wholesale_selection_rule')]

    operations = [
        migrations.CreateModel(
            name='KaspiBuyingCandidate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('product_id', models.CharField(max_length=80, unique=True)),
                ('title', models.CharField(max_length=500)),
                ('brand', models.CharField(blank=True, max_length=120)),
                ('url', models.URLField(blank=True, max_length=2048)),
                ('minimum_price', models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ('city_offers', models.JSONField(blank=True, default=list)),
                ('match_status', models.CharField(default='unmatched', max_length=24)),
                ('source_filename', models.CharField(blank=True, max_length=255)),
                ('imported_at', models.DateTimeField(auto_now=True)),
                ('offer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='buying_candidates', to='procurement.offer')),
            ],
            options={'ordering': ['-imported_at', 'pk']},
        ),
    ]
