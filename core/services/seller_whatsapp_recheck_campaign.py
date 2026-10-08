from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import SellerLead
from core.services.seller_contact_enrichment import (
    SellerContactEnrichmentError,
    active_enrichment_sources,
    enrich_seller_lead_contacts,
)

CAMPAIGN_STARTED_AT = datetime(2026, 10, 8, 10, 37, 11, tzinfo=dt_timezone.utc)
CAMPAIGN_LEAD_IDS = (
    1, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17, 19, 20, 22, 23,
    24, 25, 26, 28, 29, 31, 32, 33, 34, 35, 36, 37, 38, 40, 41, 42, 43,
    44, 46, 47, 48, 49, 50, 52, 53, 54, 55, 56, 58, 59, 60, 62, 64, 65,
    66, 68, 70, 71, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85,
    86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 99, 100, 101, 102, 103,
    104, 105, 106, 107, 109, 110, 111, 112, 113, 114, 116, 117, 118, 119,
    120, 122, 123, 124, 125, 126, 127, 128, 129,
)
CAMPAIGN_SOURCES = ('all',)
TERMINAL_LIFECYCLES = (
    SellerLead.LIFECYCLE_DUPLICATE,
    SellerLead.LIFECYCLE_REJECTED,
    SellerLead.LIFECYCLE_CLOSED,
    SellerLead.LIFECYCLE_CLAIMED,
    SellerLead.LIFECYCLE_VERIFIED,
    SellerLead.LIFECYCLE_ACTIVE,
)


@dataclass(frozen=True)
class RecheckResult:
    lead_id: int | None
    outcome: str
    found_whatsapp: bool
    active_sources: tuple[str, ...]
    remaining: int
    error: str = ''


def campaign_pending_queryset():
    return (
        SellerLead.objects
        .filter(pk__in=CAMPAIGN_LEAD_IDS, duplicate_of__isnull=True, whatsapp='')
        .exclude(lifecycle_status__in=TERMINAL_LIFECYCLES)
        .filter(
            Q(last_enrichment_attempt_at__isnull=True)
            | Q(last_enrichment_attempt_at__lt=CAMPAIGN_STARTED_AT)
        )
    )


def campaign_remaining_count() -> int:
    return campaign_pending_queryset().count()


def _claim_next_lead() -> SellerLead | None:
    with transaction.atomic():
        lead = (
            campaign_pending_queryset()
            .select_for_update(skip_locked=True)
            .order_by('id')
            .first()
        )
        if lead is None:
            return None
        # Claim before network I/O so concurrent runner calls never process
        # the same lead twice. A controlled failure is still a completed
        # recheck attempt for this one-off campaign.
        lead.last_enrichment_attempt_at = timezone.now()
        lead.save(update_fields=['last_enrichment_attempt_at', 'updated_at'])
        return lead


def recheck_next_seller_whatsapp() -> RecheckResult:
    try:
        active = tuple(active_enrichment_sources(list(CAMPAIGN_SOURCES)))
    except SellerContactEnrichmentError as exc:
        return RecheckResult(
            lead_id=None,
            outcome='configuration_error',
            found_whatsapp=False,
            active_sources=(),
            remaining=campaign_remaining_count(),
            error=str(exc),
        )

    if not active:
        return RecheckResult(
            lead_id=None,
            outcome='configuration_error',
            found_whatsapp=False,
            active_sources=(),
            remaining=campaign_remaining_count(),
            error='No active enrichment sources.',
        )

    lead = _claim_next_lead()
    if lead is None:
        return RecheckResult(
            lead_id=None,
            outcome='complete',
            found_whatsapp=False,
            active_sources=active,
            remaining=0,
        )

    try:
        result = enrich_seller_lead_contacts(
            lead,
            sources=list(CAMPAIGN_SOURCES),
            dry_run=False,
            stop_on_verified_whatsapp=True,
        )
    except Exception as exc:
        return RecheckResult(
            lead_id=lead.pk,
            outcome='error',
            found_whatsapp=False,
            active_sources=active,
            remaining=campaign_remaining_count(),
            error=f'{type(exc).__name__}: {exc}'[:400],
        )

    lead.refresh_from_db(fields=['whatsapp'])
    return RecheckResult(
        lead_id=lead.pk,
        outcome=result.outcome,
        found_whatsapp=bool(lead.whatsapp),
        active_sources=active,
        remaining=campaign_remaining_count(),
    )


def process_seller_whatsapp_recheck_batch(batch_size: int = 2) -> list[RecheckResult]:
    size = max(1, min(int(batch_size or 1), 3))
    results: list[RecheckResult] = []
    for _index in range(size):
        result = recheck_next_seller_whatsapp()
        results.append(result)
        if result.outcome in {'complete', 'configuration_error'}:
            break
    return results
