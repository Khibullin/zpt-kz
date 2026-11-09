from decimal import Decimal, InvalidOperation
from django.db import migrations
from django.db.models import F


def dates_from_evidence(apps, schema_editor):
    Offer = apps.get_model('procurement', 'Offer')
    Snapshot = apps.get_model('procurement', 'KaspiSellerSnapshot')
    Offer.objects.update(supplier_checked_on=F('checked_on'))
    # Legacy checked_on mixed supplier and Kaspi dates. Trust only a matching,
    # complete seller snapshot whose minimum equals the stored price.
    updates = []
    for snapshot in Snapshot.objects.filter(complete=True, city='750000000').select_related('offer').iterator(chunk_size=500):
        offer = snapshot.offer
        if snapshot.source_url != offer.kaspi_url or offer.kaspi_price is None:
            continue
        try:
            prices = [Decimal(str(s['price'])) for s in snapshot.sellers]
        except (KeyError, TypeError, InvalidOperation):
            continue
        if prices and all(p.is_finite() and p > 0 for p in prices) and min(prices) == offer.kaspi_price:
            offer.kaspi_price_checked_on = snapshot.checked_on
            updates.append(offer)
        if len(updates) >= 500:
            Offer.objects.bulk_update(updates, ['kaspi_price_checked_on'], batch_size=500)
            updates = []
    if updates:
        Offer.objects.bulk_update(updates, ['kaspi_price_checked_on'], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [('procurement', '0015_offer_kaspi_price_checked_on_and_more')]
    operations = [migrations.RunPython(dates_from_evidence, migrations.RunPython.noop)]
