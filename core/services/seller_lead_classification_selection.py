"""Choose SellerLead rows whose stored text should be classified again.

Selection only. This module does not call the network and does not send messages.
A newer SellerLeadEvidence or SellerLeadSource after last_classified_at is enough
for the next classification batch to pick the lead up after contact enrichment.
A fresh unknown result is not selected again until the retry interval or new data.
"""

from datetime import timedelta

from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from core.models import (
    BUSINESS_TYPE_UNKNOWN,
    SellerLead,
    SellerLeadEvidence,
    SellerLeadSource,
)

EXCLUDED_LIFECYCLES = (
    SellerLead.LIFECYCLE_DUPLICATE,
    SellerLead.LIFECYCLE_REJECTED,
    SellerLead.LIFECYCLE_CLOSED,
)
UNKNOWN_CLASSIFICATION_RETRY_DAYS = 30


def select_leads_needing_classification(*, city: str = '', business_type: str = '', lead_ids=None, limit: int = 20):
    queryset = SellerLead.objects.exclude(lifecycle_status__in=EXCLUDED_LIFECYCLES)
    if lead_ids:
        queryset = queryset.filter(pk__in=lead_ids)
    if city:
        queryset = queryset.filter(city=city)
    if business_type:
        queryset = queryset.filter(business_type=business_type)
    newer_evidence = SellerLeadEvidence.objects.filter(
        seller_lead_id=OuterRef('pk'),
        observed_at__gt=OuterRef('last_classified_at'),
    )
    newer_source = SellerLeadSource.objects.filter(
        seller_lead_id=OuterRef('pk'),
    ).filter(
        Q(last_seen_at__gt=OuterRef('last_classified_at'))
        | Q(fetched_at__gt=OuterRef('last_classified_at')),
    )
    stale_unknown_before = timezone.now() - timedelta(days=UNKNOWN_CLASSIFICATION_RETRY_DAYS)
    queryset = queryset.filter(
        Q(last_classified_at__isnull=True)
        | Q(Exists(newer_evidence))
        | Q(Exists(newer_source))
        | Q(
            business_type=BUSINESS_TYPE_UNKNOWN,
            last_classified_at__lt=stale_unknown_before,
        ),
    ).order_by(F('last_classified_at').asc(nulls_first=True), 'pk')
    return queryset[:limit]
