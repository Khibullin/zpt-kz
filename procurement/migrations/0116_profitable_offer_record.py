"""Preserve existing profitable selections without inventing historic processing dates."""
from decimal import Decimal
import re
from django.db import migrations, models
import django.db.models.deletion


def seed_existing(apps, schema_editor):
    if schema_editor.connection.alias != 'default':
        return
    Offer = apps.get_model('procurement', 'Offer')
    Preferences = apps.get_model('procurement', 'Preferences')
    Record = apps.get_model('procurement', 'ProfitableOfferRecord')
    preferences = Preferences.objects.filter(
        user__is_superuser=True, commission__isnull=False,
        expenses__isnull=False,
    ).order_by('user_id').first()
    if preferences is None:
        return  # Never guess a missing commission or expense amount.
    commission = Decimal(preferences.commission)
    expenses = max(Decimal('250'), Decimal(preferences.expenses))
    threshold = max(Decimal('1000'), Decimal(preferences.best_min_profit))
    buffer = []
    for offer in Offer.objects.filter(
        match_verified=True, kaspi_price__isnull=False, cost__gt=0
    ).only('id','name','category','cost','kaspi_price','stock',
           'supplier_checked_on','kaspi_price_checked_on').iterator(chunk_size=500):
        label = (offer.name + ' ' + offer.category).lower()
        if any(word in label for word in ('подшипник', 'bearing')):
            continue
        if not re.match(r'^\s*[><≥≤]?\s*[1-9][0-9]*(?:\s|$)', offer.stock or ''):
            continue
        profit = Decimal(offer.kaspi_price) * (Decimal('100') - commission) / Decimal('100') - Decimal(offer.cost) - expenses
        if profit < threshold:
            continue
        buffer.append(Record(
            offer_id=offer.pk, source_cost=offer.cost,
            source_kaspi_price=offer.kaspi_price, source_profit=profit,
            source_roi=profit / Decimal(offer.cost) * Decimal('100'),
            commission=commission, expenses=expenses,
            supplier_date=offer.supplier_checked_on,
            kaspi_date=offer.kaspi_price_checked_on,
            initial_import=True, processed_at=None,
        ))
        if len(buffer) >= 500:
            Record.objects.bulk_create(buffer, ignore_conflicts=True, batch_size=500)
            buffer.clear()
    if buffer:
        Record.objects.bulk_create(buffer, ignore_conflicts=True, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ('procurement', '0115_stage_hyundai_kia_553112s400_photo_fitment'),
    ]
    operations = [
        migrations.CreateModel(
            name='ProfitableOfferRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_cost', models.DecimalField(max_digits=14, decimal_places=2)),
                ('source_kaspi_price', models.DecimalField(max_digits=14, decimal_places=2)),
                ('source_profit', models.DecimalField(max_digits=18, decimal_places=2)),
                ('source_roi', models.DecimalField(max_digits=18, decimal_places=4)),
                ('commission', models.DecimalField(max_digits=5, decimal_places=2)),
                ('expenses', models.DecimalField(max_digits=12, decimal_places=2)),
                ('supplier_date', models.DateField(blank=True, null=True)),
                ('kaspi_date', models.DateField(blank=True, null=True)),
                ('captured_at', models.DateTimeField(auto_now_add=True)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
                ('initial_import', models.BooleanField(default=False)),
                ('offer', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                    related_name='profitable_record', to='procurement.offer')),
                ('first_batch', models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='new_profitable_offers', to='procurement.priceupdate')),
            ],
            options={'ordering':['offer_id']},
        ),
        migrations.RunPython(seed_existing, migrations.RunPython.noop),
    ]
