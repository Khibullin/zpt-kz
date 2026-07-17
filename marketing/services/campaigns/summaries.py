from __future__ import annotations

from marketing.services.campaigns.constants import (
    CAMPAIGN_CHANNEL_CHOICES,
    CAMPAIGN_PURPOSE_CHOICES,
    CAMPAIGN_STATUS_CHOICES,
    EXCLUSION_REASON_CHOICES,
    STATUS_AUDIENCE_STALE,
)
from marketing.services.audiences.constants import EXCLUSION_LABELS


def purpose_label(purpose: str) -> str:
    return dict(CAMPAIGN_PURPOSE_CHOICES).get(purpose, purpose)


def status_label(status: str) -> str:
    return dict(CAMPAIGN_STATUS_CHOICES).get(status, status)


def channel_label(channel: str) -> str:
    return dict(CAMPAIGN_CHANNEL_CHOICES).get(channel, channel)


def exclusion_reason_label(reason: str) -> str:
    if not reason:
        return '—'
    if reason in EXCLUSION_LABELS:
        return EXCLUSION_LABELS[reason]
    return dict(EXCLUSION_REASON_CHOICES).get(reason, reason)


def campaign_display_status(campaign) -> str:
    if campaign.is_snapshot_stale():
        return STATUS_AUDIENCE_STALE
    return campaign.status


def campaign_display_status_label(campaign) -> str:
    return status_label(campaign_display_status(campaign))
