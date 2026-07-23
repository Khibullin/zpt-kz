from __future__ import annotations

from django import template

from marketing.services.campaigns.summaries import (
    build_campaign_actions,
    campaign_display_status_label,
    campaign_list_excluded_display,
    campaign_snapshot_count_display,
    campaign_status_badge_class,
    campaign_type_badge_class,
    campaign_type_badge_label,
)

register = template.Library()


@register.filter
def campaign_status_label(campaign):
    return campaign_display_status_label(campaign)


@register.filter
def campaign_type_label(campaign):
    return campaign_type_badge_label(campaign)


@register.filter
def campaign_type_class(campaign):
    return campaign_type_badge_class(campaign)


@register.filter
def campaign_status_class(campaign):
    return campaign_status_badge_class(campaign)


@register.filter
def campaign_matched_display(campaign):
    return campaign_snapshot_count_display(campaign, 'matched_count')


@register.filter
def campaign_eligible_display(campaign):
    return campaign_snapshot_count_display(campaign, 'eligible_count')


@register.filter
def campaign_excluded_display(campaign):
    return campaign_list_excluded_display(campaign)


@register.filter
def campaign_actions(campaign):
    return build_campaign_actions(campaign)
