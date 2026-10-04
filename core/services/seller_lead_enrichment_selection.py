"""Choose the next SellerLead rows that still need WhatsApp enrichment.

Selection only. This module does not call providers and does not send messages.
"""

from datetime import timedelta

from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from core.models import SellerLead, SellerLeadContactCandidate, SellerLeadEvidence

ENRICHMENT_STALE_DAYS = 30
EXCLUDED_LIFECYCLES = (
    SellerLead.LIFECYCLE_DUPLICATE,
    SellerLead.LIFECYCLE_REJECTED,
    SellerLead.LIFECYCLE_CLOSED,
)


def select_leads_needing_enrichment(*, city: str = '', business_type: str = '', limit: int = 1):
    """Leads without a fresh verified WhatsApp whose last attempt is due.

    Verified state and enrichment freshness are separate. A verified number
    older than ENRICHMENT_STALE_DAYS can be selected again. Never-attempted
    rows sort before stale retries on PostgreSQL and SQLite.
    """
    stale_before = timezone.now() - timedelta(days=ENRICHMENT_STALE_DAYS)
    queryset = SellerLead.objects.exclude(lifecycle_status__in=EXCLUDED_LIFECYCLES)
    if city:
        queryset = queryset.filter(city=city)
    if business_type:
        queryset = queryset.filter(business_type=business_type)
    fresh_evidence = SellerLeadEvidence.objects.filter(
        seller_lead_id=OuterRef('pk'),
        field_name='whatsapp',
        is_selected=True,
        observed_at__gte=stale_before,
    ).filter(
        Q(normalized_value=OuterRef('whatsapp')) | Q(value=OuterRef('whatsapp')),
    )
    fresh_approval = SellerLeadContactCandidate.objects.filter(
        seller_lead_id=OuterRef('pk'),
        contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
        status=SellerLeadContactCandidate.STATUS_APPROVED,
        is_primary=True,
        value=OuterRef('whatsapp'),
        reviewed_at__gte=stale_before,
    )
    queryset = queryset.exclude(
        Q(whatsapp__gt='') & (Exists(fresh_evidence) | Exists(fresh_approval)),
    ).filter(
        Q(last_enrichment_attempt_at__isnull=True) | Q(last_enrichment_attempt_at__lt=stale_before),
    ).order_by(
        F('last_enrichment_attempt_at').asc(nulls_first=True),
        'pk',
    )
    return queryset[:limit]
