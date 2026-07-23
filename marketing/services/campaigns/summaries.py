from __future__ import annotations

from marketing.services.campaigns.constants import (
    CAMPAIGN_CHANNEL_CHOICES,
    CAMPAIGN_PURPOSE_CHOICES,
    CAMPAIGN_STATUS_CHOICES,
    EXCLUSION_REASON_CHOICES,
    PURPOSE_TEST_CAMPAIGN,
    STATUS_ARCHIVED,
    STATUS_AUDIENCE_PREPARED,
    STATUS_AUDIENCE_STALE,
    STATUS_CANCELLED,
    STATUS_DRAFT,
)
from marketing.services.audiences.constants import EXCLUSION_LABELS


def purpose_label(purpose: str) -> str:
    return dict(CAMPAIGN_PURPOSE_CHOICES).get(purpose, purpose)


def status_label(status: str) -> str:
    if status == STATUS_AUDIENCE_STALE:
        return 'Аудитория устарела'
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


STATUS_BADGE_CLASSES = {
    STATUS_DRAFT: 'campaign-badge--draft',
    STATUS_AUDIENCE_PREPARED: 'campaign-badge--prepared',
    STATUS_AUDIENCE_STALE: 'campaign-badge--stale',
    STATUS_CANCELLED: 'campaign-badge--cancelled',
    STATUS_ARCHIVED: 'campaign-badge--archived',
}


def campaign_status_badge_class(campaign) -> str:
    return STATUS_BADGE_CLASSES.get(campaign_display_status(campaign), 'campaign-badge--draft')


def campaign_type_badge_label(campaign) -> str:
    if campaign.purpose == PURPOSE_TEST_CAMPAIGN:
        return 'TEST'
    return 'Маркетинг'


def campaign_type_badge_class(campaign) -> str:
    if campaign.purpose == PURPOSE_TEST_CAMPAIGN:
        return 'campaign-badge--test'
    return 'campaign-badge--marketing'


def campaign_snapshot_count_display(campaign, field: str):
    if not campaign.has_prepared_snapshot:
        return '—'
    return getattr(campaign, field)


def campaign_list_excluded_display(campaign):
    if not campaign.has_prepared_snapshot:
        return '—'
    return max(0, campaign.matched_count - campaign.eligible_count)


def campaign_send_run_count(campaign) -> int:
    annotated = getattr(campaign, 'send_run_count', None)
    if annotated is not None:
        return annotated
    return campaign.send_runs.count()


def build_campaign_actions(campaign) -> dict[str, bool]:
    is_archived = campaign.status == STATUS_ARCHIVED
    is_cancelled = campaign.status == STATUS_CANCELLED
    has_send_runs = campaign_send_run_count(campaign) > 0
    return {
        'open': True,
        'edit': campaign.is_editable,
        'copy': True,
        'prepare': not is_archived and not is_cancelled,
        'archive': not is_archived,
        'cancel': not is_archived and not is_cancelled,
        'delete': not has_send_runs,
    }
