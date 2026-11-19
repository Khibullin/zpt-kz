"""Capture the first evidenced profitable result; price uploads must not erase it."""
from decimal import Decimal
from datetime import timedelta

from django.utils import timezone

from .analysis import stock_status
from .catalog_paging import MIN_PROFITABLE_COST, MIN_PROFITABLE_ROI, profitable_product_exclusions
from .models import Preferences, ProfitableOfferRecord


def owner_terms():
    """Use an actual configured owner algorithm; never invent a commission."""
    return Preferences.objects.filter(
        user__is_superuser=True, commission__isnull=False, expenses__isnull=False,
    ).order_by('user_id').first()


def capture_profitable_offer(offer, *, batch=None, terms=None):
    """Idempotent first snapshot. No Kaspi calls, external work or stale-date renewal."""
    if getattr(offer, 'pk', None) is None:
        return False
    if not offer.match_verified or offer.kaspi_price is None or offer.cost <= MIN_PROFITABLE_COST:
        return False
    if stock_status(offer.stock) != 'in_stock':
        return False
    if any(x in ((offer.name or '') + ' ' + (offer.category or '')).casefold()
           for x in ('пружин', 'spring')):
        return False
    terms = terms or owner_terms()
    if terms is None:
        return False
    today = timezone.localdate()
    if not (offer.supplier_checked_on and today - timedelta(days=7) <= offer.supplier_checked_on <= today
            and offer.kaspi_price_checked_on and today - timedelta(days=7) <= offer.kaspi_price_checked_on <= today):
        return False
    cost = Decimal(offer.cost)
    commission = Decimal(terms.commission)
    expenses = max(Decimal('250'), Decimal(terms.expenses))
    profit = Decimal(offer.kaspi_price) * (Decimal('100') - commission) / Decimal('100') - cost - expenses
    if offer.supplier.name == 'Omega':
        # A regional Kaspi quote is not certified as the lowest in Kazakhstan.
        # Preserve it on Offer for review; do not create a confirmed buying record
        # until a nationwide source is verified and explicitly supported.
        return False
    if profit < cost * MIN_PROFITABLE_ROI / Decimal('100'):
        return False
    _, created = ProfitableOfferRecord.objects.get_or_create(
        offer_id=offer.pk,
        defaults={
            'source_cost': cost, 'source_kaspi_price': offer.kaspi_price,
            'source_profit': profit, 'source_roi': profit / cost * Decimal('100'),
            'commission': commission, 'expenses': expenses,
            'supplier_date': offer.supplier_checked_on,
            'kaspi_date': offer.kaspi_price_checked_on,
            'processed_at': timezone.now() if batch else None,
            'first_batch': batch, 'initial_import': batch is None,
        },
    )
    return created
