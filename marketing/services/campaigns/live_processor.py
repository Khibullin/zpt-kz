from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from django.db import DatabaseError, transaction
from django.utils import timezone

from core.whatsapp_template_sender import send_whatsapp_template_message, wa_template_param
from marketing.models import MarketingCampaignMessage, MarketingCampaignSendRun
from marketing.services.campaigns.live_consent import recheck_live_recipient_consent
from marketing.services.campaigns.send_constants import (
    ERROR_CODE_DELIVERY_UNKNOWN,
    MESSAGE_STATUS_CANCELLED,
    MESSAGE_STATUS_FAILED,
    MESSAGE_STATUS_PROCESSING,
    MESSAGE_STATUS_QUEUED,
    MESSAGE_STATUS_SENT,
    MESSAGE_STATUS_SKIPPED,
    SEND_MODE_LIVE,
    SEND_RUN_STATUS_CANCELLED,
    SEND_RUN_STATUS_COMPLETED,
    SEND_RUN_STATUS_FAILED,
    SEND_RUN_STATUS_PARTIAL,
    SEND_RUN_STATUS_QUEUED,
    SEND_RUN_STATUS_RUNNING,
)
from marketing.services.campaigns.send_settings import (
    get_marketing_live_batch_size,
    get_marketing_live_send_interval_seconds,
    marketing_live_whatsapp_send_enabled,
)
from marketing.services.campaigns.test_send import _extract_meta_error

from marketing.services.templates.constants import META_STATUS_APPROVED

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveProcessorResult:
    processed_count: int
    sent_count: int
    failed_count: int
    skipped_count: int
    remaining_queued: int


def _build_body_parameters(template, variables: dict) -> list:
    parameters: list = []
    for variable in template.variables or []:
        key = variable.get('key', '')
        parameters.append(wa_template_param(variables.get(key, '')))
    return parameters


def _reserve_queued_messages(limit: int) -> list[int]:
    message_ids: list[int] = []
    with transaction.atomic():
        base_qs = MarketingCampaignMessage.objects.filter(
            status=MESSAGE_STATUS_QUEUED,
            send_run__mode=SEND_MODE_LIVE,
            send_run__status__in=[SEND_RUN_STATUS_QUEUED, SEND_RUN_STATUS_RUNNING],
        ).order_by('id')[:limit]
        try:
            queryset = base_qs.select_for_update(skip_locked=True)
            messages = list(queryset)
        except DatabaseError:
            queryset = base_qs.select_for_update()
            messages = list(queryset)
        for message in messages:
            message.status = MESSAGE_STATUS_PROCESSING
            message.attempted_at = timezone.now()
            message.save(update_fields=['status', 'attempted_at'])
            message_ids.append(message.pk)
            run = message.send_run
            if run.status == SEND_RUN_STATUS_QUEUED:
                run.status = SEND_RUN_STATUS_RUNNING
                run.save(update_fields=['status'])
    return message_ids


def _finalize_send_run(send_run_id: int) -> None:
    with transaction.atomic():
        send_run = MarketingCampaignSendRun.objects.select_for_update().get(pk=send_run_id)
        if send_run.status == SEND_RUN_STATUS_CANCELLED:
            return
        counts = {
            'queued': send_run.messages.filter(status=MESSAGE_STATUS_QUEUED).count(),
            'processing': send_run.messages.filter(status=MESSAGE_STATUS_PROCESSING).count(),
        }
        if counts['queued'] or counts['processing']:
            return

        sent_count = send_run.messages.filter(status=MESSAGE_STATUS_SENT).count()
        failed_count = send_run.messages.filter(status=MESSAGE_STATUS_FAILED).count()
        skipped_count = send_run.messages.filter(status=MESSAGE_STATUS_SKIPPED).count()
        cancelled_count = send_run.messages.filter(status=MESSAGE_STATUS_CANCELLED).count()

        send_run.sent_count = sent_count
        send_run.failed_count = failed_count
        send_run.skipped_count = skipped_count + cancelled_count
        send_run.queued_count = 0

        if sent_count == 0:
            final_status = SEND_RUN_STATUS_FAILED
        elif failed_count or skipped_count or cancelled_count:
            final_status = SEND_RUN_STATUS_PARTIAL
        else:
            final_status = SEND_RUN_STATUS_COMPLETED

        send_run.status = final_status
        send_run.finished_at = timezone.now()
        send_run.save(update_fields=[
            'sent_count',
            'failed_count',
            'skipped_count',
            'queued_count',
            'status',
            'finished_at',
        ])


def _process_single_message(
    message_id: int,
    *,
    send_callable=None,
) -> str:
    if send_callable is None:
        send_callable = send_whatsapp_template_message
    message = (
        MarketingCampaignMessage.objects.select_related(
            'send_run',
            'send_run__template',
            'send_run__campaign',
            'campaign_recipient',
        )
        .get(pk=message_id)
    )
    send_run = message.send_run
    template = send_run.template
    campaign = send_run.campaign

    if send_run.status == SEND_RUN_STATUS_CANCELLED:
        message.status = MESSAGE_STATUS_CANCELLED
        message.error_message = 'Run cancelled.'
        message.save(update_fields=['status', 'error_message'])
        return MESSAGE_STATUS_CANCELLED

    if not template.is_active or template.meta_status != META_STATUS_APPROVED:
        message.status = MESSAGE_STATUS_SKIPPED
        message.error_code = 'template_not_ready'
        message.error_message = 'Template not active/approved.'
        message.save(update_fields=['status', 'error_code', 'error_message'])
        return MESSAGE_STATUS_SKIPPED

    live_ok, skip_reason = recheck_live_recipient_consent(message.campaign_recipient)
    if not live_ok:
        message.status = MESSAGE_STATUS_SKIPPED
        message.error_code = skip_reason
        message.error_message = skip_reason
        message.save(update_fields=['status', 'error_code', 'error_message'])
        return MESSAGE_STATUS_SKIPPED

    body_parameters = _build_body_parameters(template, message.variables)
    try:
        result = send_callable(
            message.phone_normalized,
            template_name=message.template_name,
            template_language=message.language_code,
            body_parameters=body_parameters,
        )
    except Exception as exc:
        logger.warning(
            'Marketing LIVE send delivery unknown for message #%s campaign #%s: %s',
            message.pk,
            campaign.pk,
            exc.__class__.__name__,
        )
        message.status = MESSAGE_STATUS_FAILED
        message.error_code = ERROR_CODE_DELIVERY_UNKNOWN
        message.error_message = 'Delivery unknown — manual review required.'
        message.save(update_fields=['status', 'error_code', 'error_message'])
        return MESSAGE_STATUS_FAILED

    if result.get('ok'):
        message.status = MESSAGE_STATUS_SENT
        message.meta_message_id = str(result.get('message_id') or '')[:128]
        message.sent_at = timezone.now()
        message.error_code = ''
        message.error_message = ''
        message.save(update_fields=[
            'status',
            'meta_message_id',
            'sent_at',
            'error_code',
            'error_message',
        ])
        return MESSAGE_STATUS_SENT

    error_code, error_message = _extract_meta_error(result)
    message.status = MESSAGE_STATUS_FAILED
    message.error_code = error_code
    message.error_message = error_message
    message.save(update_fields=['status', 'error_code', 'error_message'])
    logger.warning(
        'Marketing LIVE send failed for message #%s campaign #%s code=%s',
        message.pk,
        campaign.pk,
        error_code,
    )
    return MESSAGE_STATUS_FAILED


def process_marketing_live_send_batch(
    *,
    send_callable=None,
    batch_size: int | None = None,
    interval_seconds: int | None = None,
) -> LiveProcessorResult:
    if send_callable is None:
        send_callable = send_whatsapp_template_message
    if not marketing_live_whatsapp_send_enabled():
        return LiveProcessorResult(0, 0, 0, 0, 0)

    limit = batch_size if batch_size is not None else get_marketing_live_batch_size()
    pause = interval_seconds if interval_seconds is not None else get_marketing_live_send_interval_seconds()

    message_ids = _reserve_queued_messages(limit)
    if not message_ids:
        return LiveProcessorResult(0, 0, 0, 0, _count_remaining_queued())

    sent_count = 0
    failed_count = 0
    skipped_count = 0
    touched_run_ids: set[int] = set()

    for index, message_id in enumerate(message_ids):
        if index > 0 and pause > 0:
            time.sleep(pause)

        message = MarketingCampaignMessage.objects.select_related('send_run').get(pk=message_id)
        touched_run_ids.add(message.send_run_id)
        outcome = _process_single_message(message_id, send_callable=send_callable)
        if outcome == MESSAGE_STATUS_SENT:
            sent_count += 1
        elif outcome == MESSAGE_STATUS_FAILED:
            failed_count += 1
        elif outcome in {MESSAGE_STATUS_SKIPPED, MESSAGE_STATUS_CANCELLED}:
            skipped_count += 1

    for run_id in touched_run_ids:
        _finalize_send_run(run_id)

    return LiveProcessorResult(
        processed_count=len(message_ids),
        sent_count=sent_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        remaining_queued=_count_remaining_queued(),
    )


def _count_remaining_queued() -> int:
    return MarketingCampaignMessage.objects.filter(
        status=MESSAGE_STATUS_QUEUED,
        send_run__mode=SEND_MODE_LIVE,
        send_run__status__in=[SEND_RUN_STATUS_QUEUED, SEND_RUN_STATUS_RUNNING],
    ).count()


def list_stuck_live_processing_messages() -> list[MarketingCampaignMessage]:
    """Return LIVE messages stuck in processing (manual review only)."""
    return list(
        MarketingCampaignMessage.objects.filter(
            status=MESSAGE_STATUS_PROCESSING,
            send_run__mode=SEND_MODE_LIVE,
        )
        .select_related('send_run', 'send_run__campaign')
        .order_by('attempted_at', 'id'),
    )


def mark_stuck_live_processing_as_delivery_unknown(
    *,
    message_ids: list[int] | None = None,
) -> int:
    """
    Mark stuck processing messages as delivery_unknown without Meta resend.
    Does not run during normal processor batch.
    """
    queryset = MarketingCampaignMessage.objects.filter(
        status=MESSAGE_STATUS_PROCESSING,
        send_run__mode=SEND_MODE_LIVE,
    )
    if message_ids is not None:
        queryset = queryset.filter(pk__in=message_ids)
    run_ids = list(queryset.values_list('send_run_id', flat=True).distinct())
    updated = queryset.update(
        status=MESSAGE_STATUS_FAILED,
        error_code=ERROR_CODE_DELIVERY_UNKNOWN,
        error_message='Processing interrupted — manual review required.',
    )
    for run_id in run_ids:
        _finalize_send_run(run_id)
    return updated


def cancel_live_send_run(send_run_id: int) -> None:
    with transaction.atomic():
        send_run = MarketingCampaignSendRun.objects.select_for_update().get(
            pk=send_run_id,
            mode=SEND_MODE_LIVE,
        )
        if send_run.status not in {SEND_RUN_STATUS_QUEUED, SEND_RUN_STATUS_RUNNING}:
            return
        send_run.status = SEND_RUN_STATUS_CANCELLED
        send_run.finished_at = timezone.now()
        send_run.queued_count = 0
        send_run.save(update_fields=['status', 'finished_at', 'queued_count'])
        send_run.messages.filter(status=MESSAGE_STATUS_QUEUED).update(
            status=MESSAGE_STATUS_CANCELLED,
            error_message='Run cancelled before send.',
        )
