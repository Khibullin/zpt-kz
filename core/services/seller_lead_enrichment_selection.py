"""Choose the next SellerLead rows that still need WhatsApp enrichment.

Selection only. This module does not call providers and does not send messages.
"""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from core.models import SellerLead
from core.services.seller_lead_whatsapp_state import verified_whatsapp_lead_ids

ENRICHMENT_STALE_DAYS = 30
EXCLUDED_LIFECYCLES = (
    SellerLead.LIFECYCLE_DUPLICATE,
    SellerLead.LIFECYCLE_REJECTED,
    SellerLead.LIFECYCLE_CLOSED,
)


def select_leads_needing_enrichment(*, city: str = '', business_type: str = '', limit: int = 1):
    stale_before = timezone.now() - timedelta(days=ENRICHMENT_STALE_DAYS)
    queryset = SellerLead.objects.exclude(lifecycle_status__in=EXCLUDED_LIFECYCLES)
    if city:
        queryset = queryset.filter(city=city)
    if business_type:
        queryset = queryset.filter(business_type=business_type)
    queryset = queryset.filter(
        Q(last_enriched_at__isnull=True) | Q(last_enriched_at__lt=stale_before),
    ).exclude(
        pk__in=verified_whatsapp_lead_ids(),
    ).order_by('last_enriched_at', 'pk')
    return queryset[:limit]
