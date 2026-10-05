"""Build a daily SellerLead operations summary. Does not send it.

Enrichment counters use the latest attempt inside the day. A finished pass
schedules the next one at least one day later, so one lead normally has one
result per day and last_enrichment_result matches that attempt. A worker that
dies during the short processing lease can be retried the same day; only the
latest attempt is then visible. There is no separate event history.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from core.models import (
    BUSINESS_TYPE_DISMANTLER,
    BUSINESS_TYPE_MIXED,
    BUSINESS_TYPE_NEW_PARTS,
    BUSINESS_TYPE_SERVICE_ONLY,
    BUSINESS_TYPE_SERVICE_PARTS,
    BUSINESS_TYPE_WHOLESALER,
    MARKET_SCOPE_FOREIGN,
    SellerLead,
    SellerLeadSource,
)
from core.services.seller_lead_enrichment_schedule import (
    RESULT_AMBIGUOUS,
    RESULT_CONFLICT,
    RESULT_ERROR,
    RESULT_HTTP_403,
    RESULT_NETWORK_ERROR,
    RESULT_NO_CONTACTS,
    RESULT_PHONES,
    RESULT_VERIFIED_WHATSAPP,
    due_seller_leads,
    network_error_days,
)
from core.services.seller_lead_qualification import QUALIFICATION_QUALIFIED, qualification_status
from core.services.seller_lead_whatsapp_state import WHATSAPP_VERIFIED, filter_seller_leads_by_whatsapp_state

ATTENTION_ERROR_RATE = 0.3
ATTENTION_MIN_PROCESSED = 5
ATTENTION_AMBIGUOUS = 5
ATTENTION_DUE = 100
ATTENTION_STALE_HOURS = 6
SOURCE_SILENCE_LOOKBACK_DAYS = 7


def build_seller_lead_daily_report(*, now=None) -> str:
    moment = now or timezone.now()
    since = moment - timedelta(days=1)
    classified = SellerLead.objects.filter(last_classified_at__gte=since)
    processed_ids = _processed_since(since, moment)
    due_now = due_seller_leads(moment).count()
    lines = [
        'SellerLead — сводка за сутки',
        f'новых SellerLead: {SellerLead.objects.filter(created_at__gte=since).count()}',
        f'классифицировано: {classified.count()}',
        f'qualified: {_qualified_in(classified)}',
        f'auto_dismantler: {classified.filter(business_type=BUSINESS_TYPE_DISMANTLER).count()}',
        f'parts_store: {classified.filter(business_type=BUSINESS_TYPE_NEW_PARTS).count()}',
        f'wholesaler: {classified.filter(business_type=BUSINESS_TYPE_WHOLESALER).count()}',
        (
            'service/mixed: '
            f'{classified.filter(business_type__in=[BUSINESS_TYPE_SERVICE_PARTS, BUSINESS_TYPE_SERVICE_ONLY, BUSINESS_TYPE_MIXED]).count()}'
        ),
        f'foreign: {classified.filter(market_scope=MARKET_SCOPE_FOREIGN).count()}',
        (
            'rejected/not-target: '
            f'{SellerLead.objects.filter(rejected_at__gte=since).count()}'
        ),
        f'enrichment processed: {processed_ids.count()}',
        f'verified WhatsApp найдено: {_result_count(processed_ids, RESULT_VERIFIED_WHATSAPP)}',
        f'phones найдено: {_result_count(processed_ids, RESULT_PHONES)}',
        f'no contacts: {_result_count(processed_ids, RESULT_NO_CONTACTS)}',
        f'ambiguous: {_result_count(processed_ids, RESULT_AMBIGUOUS)}',
        f'identity conflict: {_result_count(processed_ids, RESULT_CONFLICT)}',
        f'HTTP 403: {_result_count(processed_ids, RESULT_HTTP_403)}',
        f'timeout/network errors: {_result_count(processed_ids, RESULT_NETWORK_ERROR)}',
        f'failed/unexpected errors: {_result_count(processed_ids, RESULT_ERROR)}',
        f'due сейчас: {due_now}',
        f'backlog: {due_now}',
        f'всего SellerLead: {SellerLead.objects.count()}',
        f'всего verified WhatsApp: {_verified_total()}',
        '',
        'Требует внимания',
    ]
    lines.extend(_attention(
        moment=moment,
        since=since,
        processed=processed_ids.count(),
        http_403=_result_count(processed_ids, RESULT_HTTP_403),
        network=_result_count(processed_ids, RESULT_NETWORK_ERROR),
        failed=_result_count(processed_ids, RESULT_ERROR),
        ambiguous=_result_count(processed_ids, RESULT_AMBIGUOUS),
        due_now=due_now,
    ))
    return '\n'.join(lines)


def _processed_since(since, moment):
    """Attempts dated in the window, plus a command failure that did not stamp one.

    A scheduler failure stores last_enrichment_result=error and pushes
    next_enrichment_at by the network-error interval. That interval dates the
    failure without treating a later edit of updated_at as a new event.
    """
    error_until = moment + timedelta(days=network_error_days())
    return SellerLead.objects.filter(
        Q(last_enrichment_attempt_at__gte=since)
        | Q(
            last_enrichment_result=RESULT_ERROR,
            last_enrichment_attempt_at__isnull=True,
            next_enrichment_at__gte=moment,
            next_enrichment_at__lte=error_until,
        ),
    )


def _result_count(processed, code: str) -> int:
    return processed.filter(last_enrichment_result=code).count()


def _qualified_in(classified) -> int:
    total = 0
    for lead in classified.iterator():
        if qualification_status(lead) == QUALIFICATION_QUALIFIED:
            total += 1
    return total


def _verified_total() -> int:
    return filter_seller_leads_by_whatsapp_state(SellerLead.objects.all(), WHATSAPP_VERIFIED).count()


def _attention(*, moment, since, processed, http_403, network, failed, ambiguous, due_now) -> list[str]:
    notes = []
    latest = SellerLead.objects.exclude(
        last_enrichment_attempt_at__isnull=True,
    ).order_by('-last_enrichment_attempt_at').values_list('last_enrichment_attempt_at', flat=True).first()
    if due_now >= ATTENTION_DUE and (latest is None or latest < moment - timedelta(hours=ATTENTION_STALE_HOURS)):
        notes.append('scheduler давно не запускался: очередь due есть, свежих попыток нет')
    if due_now >= ATTENTION_DUE:
        notes.append('backlog растёт: due сейчас больше обычного batch')
    errors = http_403 + network + failed
    if processed >= ATTENTION_MIN_PROCESSED and errors / processed >= ATTENTION_ERROR_RATE:
        notes.append('вырос error rate обогащения за сутки')
    if ambiguous >= ATTENTION_AMBIGUOUS:
        notes.append('много ambiguous за сутки')
    silent = _silent_sources(since)
    if silent:
        notes.append('источник перестал работать: ' + ', '.join(silent))
    if not notes:
        notes.append('нет')
    return notes


def _silent_sources(since) -> list[str]:
    """Sources seen during the previous week and silent in the last day."""
    week_start = since - timedelta(days=SOURCE_SILENCE_LOOKBACK_DAYS)
    recent = set(
        SellerLeadSource.objects.filter(last_seen_at__gte=since).values_list('source_type', flat=True),
    )
    previous = set(
        SellerLeadSource.objects.filter(
            last_seen_at__gte=week_start,
            last_seen_at__lt=since,
        ).values_list('source_type', flat=True),
    )
    return sorted(previous - recent)
