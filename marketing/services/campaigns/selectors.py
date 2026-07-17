from __future__ import annotations

from django.contrib.auth.models import User
from django.db.models import QuerySet
from django.utils.dateparse import parse_date

from marketing.models import MarketingCampaign, MarketingCampaignRecipient
from marketing.services.campaigns.constants import (
    CAMPAIGN_LIST_PAGE_SIZE,
    CAMPAIGN_PREVIEW_LIMIT,
    ELIGIBILITY_ELIGIBLE,
    ELIGIBILITY_EXCLUDED,
)


def campaign_list_queryset() -> QuerySet[MarketingCampaign]:
    return MarketingCampaign.objects.select_related('audience', 'created_by').order_by(
        '-created_at',
        '-id',
    )


def filter_campaign_list(queryset: QuerySet[MarketingCampaign], params) -> QuerySet[MarketingCampaign]:
    status = params.get('status', '').strip()
    if status:
        queryset = queryset.filter(status=status)

    purpose = params.get('purpose', '').strip()
    if purpose:
        queryset = queryset.filter(purpose=purpose)

    audience_id = params.get('audience', '').strip()
    if audience_id.isdigit():
        queryset = queryset.filter(audience_id=int(audience_id))

    author_id = params.get('author', '').strip()
    if author_id.isdigit():
        queryset = queryset.filter(created_by_id=int(author_id))

    created_from = parse_date(params.get('created_from', '').strip())
    if created_from:
        queryset = queryset.filter(created_at__date__gte=created_from)

    created_to = parse_date(params.get('created_to', '').strip())
    if created_to:
        queryset = queryset.filter(created_at__date__lte=created_to)

    return queryset


def campaign_authors_queryset() -> QuerySet[User]:
    author_ids = (
        MarketingCampaign.objects.exclude(created_by_id__isnull=True)
        .values_list('created_by_id', flat=True)
        .distinct()
    )
    return User.objects.filter(pk__in=author_ids).order_by('username')


def campaign_recipient_preview(
    campaign: MarketingCampaign,
    *,
    preview_filter: str = 'all',
) -> list[MarketingCampaignRecipient]:
    queryset = campaign.recipients.all().order_by('-last_activity_at', 'id')
    if preview_filter == 'eligible':
        queryset = queryset.filter(eligibility_status=ELIGIBILITY_ELIGIBLE)
    elif preview_filter == 'excluded':
        queryset = queryset.filter(eligibility_status=ELIGIBILITY_EXCLUDED)
    return list(queryset[:CAMPAIGN_PREVIEW_LIMIT])
