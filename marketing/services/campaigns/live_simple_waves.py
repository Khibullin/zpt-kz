from __future__ import annotations

from datetime import timedelta

from django.db.models import Max, Min
from django.utils import timezone

from marketing.models import MarketingCampaignMessage, MarketingCampaignSendRun
from marketing.services.campaigns.send_constants import (
    MESSAGE_STATUS_CANCELLED,
    MESSAGE_STATUS_FAILED,
    MESSAGE_STATUS_PROCESSING,
    MESSAGE_STATUS_QUEUED,
    MESSAGE_STATUS_SENT,
    MESSAGE_STATUS_SKIPPED,
    MESSAGE_STATUS_TERMINAL,
    SEND_MODE_LIVE,
    SEND_RUN_STATUS_QUEUED,
    SEND_RUN_STATUS_RUNNING,
    WORKFLOW_TYPE_SIMPLE_MAILING,
)
from marketing.services.campaigns.send_settings import get_marketing_simple_wave_interval_minutes


def _message_terminal_timestamp(message: MarketingCampaignMessage):
    if message.status == MESSAGE_STATUS_SENT:
        return message.sent_at
    if message.status in {
        MESSAGE_STATUS_FAILED,
        MESSAGE_STATUS_SKIPPED,
        MESSAGE_STATUS_CANCELLED,
    }:
        return message.attempted_at or message.created_at
    return None


def wave_is_terminal(messages: list[MarketingCampaignMessage]) -> bool:
    if not messages:
        return True
    for message in messages:
        if message.status in {MESSAGE_STATUS_QUEUED, MESSAGE_STATUS_PROCESSING}:
            return False
        if message.status not in MESSAGE_STATUS_TERMINAL:
            return False
    return True


def wave_actual_terminal_time(messages: list[MarketingCampaignMessage]):
    if not wave_is_terminal(messages):
        return None
    timestamps = [
        ts
        for message in messages
        if (ts := _message_terminal_timestamp(message)) is not None
    ]
    if not timestamps:
        return None
    return max(timestamps)


def compute_wave_not_before(
    *,
    send_run: MarketingCampaignSendRun,
    wave_number: int,
    now,
) -> timezone.datetime:
    interval = timedelta(minutes=get_marketing_simple_wave_interval_minutes())
    precomputed = (
        send_run.messages.filter(wave_number=wave_number)
        .aggregate(value=Min('scheduled_at'))
        .get('value')
    )
    if wave_number <= 1:
        return precomputed or now
    previous_messages = list(
        send_run.messages.filter(wave_number=wave_number - 1).order_by('position_number', 'id')
    )
    previous_terminal = wave_actual_terminal_time(previous_messages)
    if previous_terminal is None:
        return timezone.datetime.max.replace(tzinfo=now.tzinfo)
    actual_gate = previous_terminal + interval
    if precomputed is None:
        return actual_gate
    return max(precomputed, actual_gate)


def get_next_eligible_simple_mailing_wave(
    send_run: MarketingCampaignSendRun,
    *,
    now=None,
) -> int | None:
    now = now or timezone.now()
    next_wave = (
        send_run.messages.filter(status=MESSAGE_STATUS_QUEUED)
        .aggregate(value=Min('wave_number'))
        .get('value')
    )
    if next_wave is None:
        return None

    for wave_number in range(1, next_wave):
        wave_messages = list(
            send_run.messages.filter(wave_number=wave_number).order_by('position_number', 'id')
        )
        if not wave_is_terminal(wave_messages):
            return None

    not_before = compute_wave_not_before(
        send_run=send_run,
        wave_number=next_wave,
        now=now,
    )
    if now < not_before:
        return None
    return next_wave


def find_active_simple_mailing_run() -> MarketingCampaignSendRun | None:
    return (
        MarketingCampaignSendRun.objects.filter(
            workflow_type=WORKFLOW_TYPE_SIMPLE_MAILING,
            mode=SEND_MODE_LIVE,
            status__in=[SEND_RUN_STATUS_QUEUED, SEND_RUN_STATUS_RUNNING],
        )
        .order_by('created_at', 'id')
        .first()
    )


def simple_mailing_has_active_run() -> bool:
    return MarketingCampaignSendRun.objects.filter(
        workflow_type=WORKFLOW_TYPE_SIMPLE_MAILING,
        mode=SEND_MODE_LIVE,
        status__in=[SEND_RUN_STATUS_QUEUED, SEND_RUN_STATUS_RUNNING],
        messages__status__in=[MESSAGE_STATUS_QUEUED, MESSAGE_STATUS_PROCESSING],
    ).exists()


def get_simple_mailing_wave_display(send_run: MarketingCampaignSendRun) -> dict | None:
    if send_run.workflow_type != WORKFLOW_TYPE_SIMPLE_MAILING:
        return None
    total_waves = (
        send_run.messages.aggregate(value=Max('wave_number')).get('value') or 0
    )
    if total_waves <= 0:
        return None
    current_wave = (
        send_run.messages.filter(status=MESSAGE_STATUS_QUEUED)
        .aggregate(value=Min('wave_number'))
        .get('value')
    )
    if current_wave is None:
        current_wave = total_waves
    next_not_before = compute_wave_not_before(
        send_run=send_run,
        wave_number=current_wave,
        now=timezone.now(),
    )
    return {
        'total_waves': total_waves,
        'current_wave': current_wave,
        'next_not_before': next_not_before,
    }
