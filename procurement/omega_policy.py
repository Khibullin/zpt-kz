"""Supplier-specific eligibility for scheduled Kaspi lookups.

Never infer product identity or nation-wide seller coverage.
"""
from decimal import Decimal
from django.db.models import Q

OMEGA_MIN_COST = Decimal('2000')
OMEGA_MIN_NET_ROI = Decimal('50')
POSITIVE_STOCK_PATTERN = r'^\s*(?:[>≥]\s*)?[1-9][0-9]*(?:>|\s|$)'


def eligible_scheduled_kaspi_tasks(queryset):
    """Restrict automated Omega requests to confirmed positive supplier stock
    and >= 2,000 KZT cost. All other suppliers retain their existing behavior.
    """
    return queryset.filter(
        ~Q(offer__supplier__name='Omega') |
        (Q(offer__cost__gte=OMEGA_MIN_COST) &
         Q(offer__stock__regex=POSITIVE_STOCK_PATTERN))
    )
