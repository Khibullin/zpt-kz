from __future__ import annotations

from typing import TYPE_CHECKING

from marketing.services.templates.selectors import (
    template_allows_purpose,
    template_is_selectable,
)

if TYPE_CHECKING:
    from marketing.models import MarketingCampaign


def build_campaign_readiness(campaign: MarketingCampaign) -> dict:
    template = campaign.message_template
    audience_selected = campaign.audience_id is not None
    snapshot_prepared = campaign.has_prepared_snapshot
    snapshot_fresh = snapshot_prepared and not campaign.is_snapshot_stale()
    has_eligible = campaign.eligible_count > 0
    template_selected = template is not None
    template_active = bool(template and template.is_active)
    template_approved = bool(template and template.is_selectable_for_campaign)
    template_compatible = bool(
        template
        and template_allows_purpose(template, campaign.purpose),
    )
    template_usable = bool(
        template
        and template_is_selectable(template)
        and template_allows_purpose(template, campaign.purpose),
    )

    items = [
        {
            'key': 'audience_selected',
            'label': 'Аудитория выбрана',
            'ok': audience_selected,
        },
        {
            'key': 'snapshot_prepared',
            'label': 'Получатели подготовлены',
            'ok': snapshot_prepared,
        },
        {
            'key': 'snapshot_fresh',
            'label': 'Snapshot актуален',
            'ok': snapshot_fresh,
        },
        {
            'key': 'eligible_recipients',
            'label': 'Есть допустимые получатели',
            'ok': has_eligible,
        },
        {
            'key': 'template_selected',
            'label': 'Шаблон выбран',
            'ok': template_selected,
        },
        {
            'key': 'template_active',
            'label': 'Шаблон активен',
            'ok': template_active,
        },
        {
            'key': 'template_approved',
            'label': 'Шаблон approved',
            'ok': template_approved,
        },
        {
            'key': 'template_compatible',
            'label': 'Шаблон совместим',
            'ok': template_compatible,
        },
    ]

    recipients_ready = all(
        item['ok']
        for item in items
        if item['key'] in {
            'audience_selected',
            'snapshot_prepared',
            'snapshot_fresh',
            'eligible_recipients',
        }
    )
    template_ready = all(
        item['ok']
        for item in items
        if item['key'] in {
            'template_selected',
            'template_active',
            'template_approved',
            'template_compatible',
        }
    )
    prepared_for_next_stage = recipients_ready and template_ready

    return {
        'items': items,
        'recipients_ready': recipients_ready,
        'template_ready': template_ready,
        'prepared_for_next_stage': prepared_for_next_stage,
        'template_usable': template_usable,
        'template_unavailable_message': (
            'Выбранный шаблон сейчас недоступен для использования'
            if template_selected and not template_usable
            else ''
        ),
    }
