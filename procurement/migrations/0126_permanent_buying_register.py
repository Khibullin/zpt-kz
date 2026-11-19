from decimal import Decimal
import re

from django.db import migrations, models
import django.db.models.deletion


def preserve_existing_buying_items(apps, schema_editor):
    """One-time, append-only migration of historical deals and Apify matches."""
    Item = apps.get_model('procurement', 'PermanentBuyingItem')
    Record = apps.get_model('procurement', 'ProfitableOfferRecord')
    Candidate = apps.get_model('procurement', 'KaspiBuyingCandidate')
    Offer = apps.get_model('procurement', 'Offer')
    Pref = apps.get_model('procurement', 'Preferences')
    Activity = apps.get_model('procurement', 'Activity')
    db = schema_editor.connection.alias
    present = set()
    batch = []

    def add(data):
        if data['offer_id'] in present:
            return
        present.add(data['offer_id'])
        batch.append(Item(**data))
        if len(batch) >= 400:
            Item.objects.using(db).bulk_create(batch, ignore_conflicts=True, batch_size=400)
            batch.clear()

    for rec in Record.objects.using(db).select_related('offer').iterator(chunk_size=500):
        offer = rec.offer
        if offer.supplier.name != 'Omega':
            continue
        add(dict(offer_id=offer.pk, source='historical', title=offer.name,
                 kaspi_url=offer.kaspi_url or '',
                 cost_snapshot=rec.source_cost,
                 kaspi_price_snapshot=rec.source_kaspi_price,
                 commission_snapshot=rec.commission,
                 expenses_snapshot=rec.expenses,
                 profit_snapshot=rec.source_profit,
                 roi_snapshot=rec.source_roi,
                 identity_status='historical',
                 photo_url=offer.photo_url or ''))

    session = (Activity.objects.using(db).filter(action='apify_upload_session')
               .order_by('-id').first())
    preferences = (Pref.objects.using(db).filter(user_id=session.user_id).first()
                   if session and session.user_id else None)
    if preferences is None:
        preferences = Pref.objects.using(db).filter(
            commission__isnull=False, expenses__isnull=False).order_by('-pk').first()
    if preferences and preferences.commission is not None and preferences.expenses is not None:
        commission = Decimal(preferences.commission)
        expenses = max(Decimal('250'), Decimal(preferences.expenses))
        valid_stock = re.compile(r'^\s*(?:[>≥]\s*)?[1-9][0-9]*(?:>|\s|$)')
        spring = re.compile(r'пружин|spring', re.I)
        consumable = ('расходник', 'фильтр', 'масло', 'свеча', 'щетк', 'щётк',
                      'ремень', 'filter', 'oil', 'spark plug', 'wiper', 'belt')
        for offer in Offer.objects.using(db).filter(
                supplier__name='Omega', match_verified=True,
                kaspi_price__isnull=False).iterator(chunk_size=500):
            if offer.pk in present:
                continue
            label = (offer.name + ' ' + offer.category).casefold()
            if spring.search(label) or not valid_stock.match(offer.stock or ''):
                continue
            cost = offer.cost
            threshold_ok = cost >= 2000 if any(x in label for x in consumable) else cost > 5000
            if not threshold_ok or cost <= 0:
                continue
            price = offer.kaspi_price
            profit = price * (1 - commission / 100) - cost - expenses
            if profit < cost * Decimal('.5'):
                continue
            add(dict(offer_id=offer.pk, source='historical', title=offer.name,
                     kaspi_url=offer.kaspi_url or '', cost_snapshot=cost,
                     kaspi_price_snapshot=price, commission_snapshot=commission,
                     expenses_snapshot=expenses,
                     profit_snapshot=profit.quantize(Decimal('.01')),
                     roi_snapshot=(profit * 100 / cost).quantize(Decimal('.0001')),
                     identity_status='historical',
                     photo_url=offer.photo_url or ''))

        # Previously accepted candidate rows are preserved instead of forcing
        # reupload. Do not change their existing identity verification status.
        for candidate in Candidate.objects.using(db).filter(
                offer__supplier__name='Omega',
                match_status__in=['candidate', 'verified'],
                offer__isnull=False, minimum_price__isnull=False).select_related(
                'offer').iterator(chunk_size=500):
            offer = candidate.offer
            if offer.pk in present or not valid_stock.match(offer.stock or ''):
                continue
            label = (offer.name + ' ' + offer.category + ' ' + candidate.title).casefold()
            if spring.search(label):
                continue
            cost = offer.cost
            threshold_ok = cost >= 2000 if any(x in label for x in consumable) else cost > 5000
            if not threshold_ok or cost <= 0:
                continue
            price = candidate.minimum_price
            profit = price * (1 - commission / 100) - cost - expenses
            if profit < cost * Decimal('.5'):
                continue
            add(dict(offer_id=offer.pk, source='apify', product_id=candidate.product_id,
                     title=candidate.title or offer.name,
                     kaspi_url=candidate.url or '',
                     cost_snapshot=cost, kaspi_price_snapshot=price,
                     commission_snapshot=commission, expenses_snapshot=expenses,
                     profit_snapshot=profit.quantize(Decimal('.01')),
                     roi_snapshot=(profit * 100 / cost).quantize(Decimal('.0001')),
                     identity_status=candidate.match_status,
                     photo_url=offer.photo_url or ''))
    if batch:
        Item.objects.using(db).bulk_create(batch, ignore_conflicts=True, batch_size=400)


class Migration(migrations.Migration):
    dependencies = [('procurement', '0125_rematch_apify_brand_article')]
    operations = [
        migrations.CreateModel(
            name='PermanentBuyingItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source', models.CharField(default='historical', max_length=16)),
                ('product_id', models.CharField(blank=True, max_length=80)),
                ('title', models.CharField(max_length=500)),
                ('kaspi_url', models.URLField(blank=True, max_length=2048)),
                ('cost_snapshot', models.DecimalField(decimal_places=2, max_digits=14)),
                ('kaspi_price_snapshot', models.DecimalField(decimal_places=2, max_digits=14)),
                ('commission_snapshot', models.DecimalField(decimal_places=2, max_digits=6)),
                ('expenses_snapshot', models.DecimalField(decimal_places=2, max_digits=14)),
                ('profit_snapshot', models.DecimalField(decimal_places=2, max_digits=18)),
                ('roi_snapshot', models.DecimalField(decimal_places=4, max_digits=18)),
                ('identity_status', models.CharField(default='historical', max_length=24)),
                ('photo_url', models.URLField(blank=True, max_length=2048)),
                ('included_at', models.DateTimeField(auto_now_add=True)),
                ('offer', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                                              related_name='permanent_buying_item',
                                              to='procurement.offer')),
            ],
            options={'ordering': ['-profit_snapshot', '-roi_snapshot', 'pk']},
        ),
        migrations.RunPython(preserve_existing_buying_items, migrations.RunPython.noop),
    ]
