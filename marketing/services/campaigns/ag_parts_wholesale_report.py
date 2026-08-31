from __future__ import annotations

from collections import Counter

from marketing.models import (
    MarketingCampaign,
    MarketingCampaignMessage,
    MarketingCampaignSendRun,
)
from marketing.services.audiences.builders import build_seller_source_index
from marketing.services.audiences.calculators import collect_audience_snapshot
from marketing.services.audiences.filters import values_intersect
from marketing.services.campaigns.preparation import prepare_campaign_snapshot
from marketing.services.campaigns.send_constants import (
    MESSAGE_STATUS_LIVE_DELIVERED,
    SEND_RUN_LIVE_ACTIVE_STATUSES,
)

CAMPAIGN_NAME = 'AG Parts — запуск оптовой витрины — 08.2026'
NEW_AUDIENCE_NAME = 'AG Parts — Китай или все марки — 08.2026'
CHINA_COUNTRY = 'Китай'


def _china_all_brands_breakdown(matched_phones: list[str]) -> dict[str, int]:
    seller_index = build_seller_source_index()
    china = 0
    all_brands = 0
    intersection = 0
    for phone in matched_phones:
        flags = seller_index.get(phone)
        if flags is None:
            continue
        has_china = values_intersect(
            set(flags.selected_country_names),
            [CHINA_COUNTRY],
        )
        has_all_brands = bool(flags.all_brands)
        if has_china:
            china += 1
        if has_all_brands:
            all_brands += 1
        if has_china and has_all_brands:
            intersection += 1
    return {
        'china_count': china,
        'all_brands_count': all_brands,
        'intersection_count': intersection,
    }


def build_ag_parts_wholesale_audience_report(*, prepare: bool = False) -> dict:
    campaign = MarketingCampaign.objects.select_related('audience').get(name=CAMPAIGN_NAME)
    audience = campaign.audience
    if audience is None:
        raise ValueError(f'Campaign «{CAMPAIGN_NAME}» has no audience.')

    sent_count = MarketingCampaignMessage.objects.filter(
        send_run__campaign=campaign,
        status__in=MESSAGE_STATUS_LIVE_DELIVERED,
    ).count()
    active_runs = MarketingCampaignSendRun.objects.filter(
        campaign=campaign,
        status__in=SEND_RUN_LIVE_ACTIVE_STATUSES,
    ).count()
    if sent_count or active_runs:
        raise RuntimeError(
            'Refuse to prepare: campaign already has sent messages or an active send run.',
        )

    snapshot = collect_audience_snapshot(
        contact_group=audience.contact_group,
        contact_subtype=audience.contact_subtype,
        criteria=audience.criteria,
        purpose=campaign.purpose,
    )
    matched_phones = [row.phone_normalized for row in snapshot.contacts]
    exclusions = Counter(
        row.exclusion_reason or 'eligible'
        for row in snapshot.contacts
        if row.eligibility_status != 'eligible'
    )

    if prepare:
        campaign = prepare_campaign_snapshot(campaign.pk)
        campaign.refresh_from_db()
        snapshot_eligible = campaign.eligible_count
        snapshot_excluded = campaign.excluded_count
        snapshot_matched = campaign.matched_count
        unique_phones = campaign.unique_count
    else:
        snapshot_eligible = snapshot.eligible_count
        snapshot_excluded = snapshot.excluded_count
        snapshot_matched = snapshot.matched_count
        unique_phones = snapshot.unique_count

    sent_after = MarketingCampaignMessage.objects.filter(
        send_run__campaign=campaign,
        status__in=MESSAGE_STATUS_LIVE_DELIVERED,
    ).count()

    report = {
        'campaign_name': campaign.name,
        'campaign_purpose': campaign.purpose,
        'audience_name': audience.name,
        'matched_count': snapshot_matched,
        'eligible_count': snapshot_eligible,
        'excluded_count': snapshot_excluded,
        'unique_phones': unique_phones,
        'exclusion_reasons': dict(exclusions),
        'messages_sent': sent_after,
        'prepared': prepare,
        **_china_all_brands_breakdown(matched_phones),
    }
    return report
