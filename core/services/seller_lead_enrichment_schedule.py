"""When a SellerLead is due for contact enrichment.

Intervals live here and can be overridden from Django settings.
The scheduler calls enrich_seller_lead_contacts(); this module only chooses
the next time and the stored result code.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from core.models import MARKET_SCOPE_KZ, SellerLead
from core.services.seller_lead_enrichment_selection import EXCLUDED_LIFECYCLES
from core.services.seller_lead_qualification import TARGET_BUSINESS_TYPES

RESULT_VERIFIED_WHATSAPP = 'verified_whatsapp'
RESULT_NO_CONTACTS = 'no_contacts'
RESULT_PHONES = 'phones'
RESULT_NETWORK_ERROR = 'network_error'
RESULT_HTTP_403 = 'http_403'
RESULT_AMBIGUOUS = 'ambiguous'
RESULT_ERROR = 'error'

_NETWORK_MARKERS = (
    'timeout',
    'connection_reset',
    'ssl_error',
    'network_error',
    'соединение сброшено',
    'timed out',
)
_HTTP_403_MARKERS = ('http_403', 'http 403')
_AMBIGUOUS_MARKERS = ('ambiguous',)


def _days(setting_name: str, default: int) -> int:
    raw = getattr(settings, setting_name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def verified_whatsapp_days() -> int:
    return _days('SELLER_LEAD_VERIFIED_WHATSAPP_DAYS', 90)


def no_contact_first_days() -> int:
    return _days('SELLER_LEAD_NO_CONTACT_FIRST_DAYS', 7)


def no_contact_repeat_days() -> int:
    return _days('SELLER_LEAD_NO_CONTACT_REPEAT_DAYS', 30)


def network_error_days() -> int:
    return _days('SELLER_LEAD_NETWORK_ERROR_DAYS', 1)


def processing_lease_minutes() -> int:
    """How long a claimed row stays hidden if the worker dies before the result.

    This is not the retry schedule. A finished pass replaces it.
    """
    raw = getattr(settings, 'SELLER_LEAD_PROCESSING_LEASE_MINUTES', 90)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 90
    return value if value >= 1 else 90


def http_403_days() -> int:
    return _days('SELLER_LEAD_HTTP_403_DAYS', 7)


def ambiguous_days() -> int:
    return _days('SELLER_LEAD_AMBIGUOUS_DAYS', 7)


def due_seller_leads(now: datetime | None = None):
    """Qualified Kazakhstan leads whose next enrichment time has arrived."""
    moment = now or timezone.now()
    return SellerLead.objects.filter(
        market_scope=MARKET_SCOPE_KZ,
        business_type__in=TARGET_BUSINESS_TYPES,
        next_enrichment_at__isnull=False,
        next_enrichment_at__lte=moment,
        request_seller__isnull=True,
        duplicate_of__isnull=True,
    ).exclude(
        lifecycle_status__in=EXCLUDED_LIFECYCLES,
    ).order_by('next_enrichment_at', 'pk')


def claim_due_seller_leads(*, limit: int, now: datetime | None = None) -> list[SellerLead]:
    """Lock due rows and push their next time forward so a second runner skips them.

    The lease is only long enough to cover a crash or a second worker.
    A finished pass replaces it with the normal interval.
    SQLite has no skip_locked; the lease is what keeps a rerun from taking the same row.
    """
    moment = now or timezone.now()
    lease_until = moment + timedelta(minutes=processing_lease_minutes())
    skip_locked = connection.vendor == 'postgresql'
    with transaction.atomic():
        queryset = due_seller_leads(moment)
        if skip_locked:
            queryset = queryset.select_for_update(skip_locked=True)
        else:
            queryset = queryset.select_for_update()
        leads = list(queryset[:limit])
        for lead in leads:
            lead.next_enrichment_at = lease_until
            lead.save(update_fields=['next_enrichment_at', 'updated_at'])
    return leads


def _text_has(texts: list[str], markers: tuple[str, ...]) -> bool:
    folded = ' '.join(texts).casefold()
    return any(marker in folded for marker in markers)


def classify_enrichment_result(result) -> str:
    """Map an enrichment result onto a schedule code. Does not write."""
    verified = list(getattr(result, 'verified_whatsapp', None) or [])
    if verified:
        return RESULT_VERIFIED_WHATSAPP
    errors = [str(item) for item in (getattr(result, 'errors', None) or [])]
    outcome = str(getattr(result, 'outcome', '') or '')
    runs = getattr(result, 'source_runs', None) or []
    run_text = [f'{getattr(run, "status", "")} {getattr(run, "detail", "")}' for run in runs]
    combined = [outcome, *errors, *run_text]
    if _text_has(combined, _AMBIGUOUS_MARKERS):
        return RESULT_AMBIGUOUS
    if _text_has(combined, _HTTP_403_MARKERS):
        return RESULT_HTTP_403
    if _text_has(combined, _NETWORK_MARKERS):
        return RESULT_NETWORK_ERROR
    observations = list(getattr(result, 'observations', None) or [])
    pending = list(getattr(result, 'pending_candidates', None) or [])
    phone_fields = {'phone', 'phones', 'whatsapp'}
    found_phone = pending or any(getattr(item, 'field_name', '') in phone_fields for item in observations)
    if found_phone or outcome == 'enriched':
        return RESULT_PHONES
    return RESULT_NO_CONTACTS


def apply_enrichment_schedule(lead: SellerLead, code: str, *, now: datetime | None = None) -> SellerLead:
    """Set the next enrichment time from a completed pass. Does not save."""
    moment = now or timezone.now()
    count = int(lead.enrichment_attempt_count or 0)
    if code == RESULT_VERIFIED_WHATSAPP:
        delay = verified_whatsapp_days()
    elif code == RESULT_NETWORK_ERROR or code == RESULT_ERROR:
        delay = network_error_days()
    elif code == RESULT_HTTP_403:
        delay = http_403_days()
    elif code == RESULT_AMBIGUOUS:
        delay = ambiguous_days()
    elif code == RESULT_NO_CONTACTS:
        delay = no_contact_first_days() if count == 0 else no_contact_repeat_days()
        count += 1
    elif code == RESULT_PHONES:
        delay = no_contact_repeat_days()
        count += 1
    else:
        delay = no_contact_first_days()
    lead.last_enrichment_result = code[:32]
    lead.enrichment_attempt_count = count
    lead.next_enrichment_at = moment + timedelta(days=delay)
    return lead
