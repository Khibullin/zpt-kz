from __future__ import annotations

from marketing.services.simple_mailing.purpose import recipient_type_to_campaign_purpose
from marketing.services.simple_mailing.template_preview import (
    get_language_label,
    render_simple_mailing_template_preview,
)
from marketing.services.templates.selectors import (
    compatible_templates_for_purpose,
    resolve_template_from_post,
    template_is_compatible_with_campaign,
)
from marketing.services.templates.validation import TemplateValidationError


def campaign_purpose_for_recipient_type(recipient_type: str) -> str:
    return recipient_type_to_campaign_purpose(recipient_type)


def compatible_templates_for_recipient_type(recipient_type: str):
    purpose = campaign_purpose_for_recipient_type(recipient_type)
    return compatible_templates_for_purpose(purpose)


def build_template_card(template) -> dict:
    return {
        'template': template,
        'preview': render_simple_mailing_template_preview(template),
        'language_label': get_language_label(template.language_code),
    }


def build_template_cards(recipient_type: str) -> list[dict]:
    return [
        build_template_card(template)
        for template in compatible_templates_for_recipient_type(recipient_type)
    ]


def resolve_selected_template(template_id: str, *, recipient_type: str):
    purpose = campaign_purpose_for_recipient_type(recipient_type)
    return resolve_template_from_post(template_id, purpose=purpose)


def template_still_available(template, *, recipient_type: str) -> bool:
    purpose = campaign_purpose_for_recipient_type(recipient_type)
    return template_is_compatible_with_campaign(template, purpose=purpose)


__all__ = [
    'TemplateValidationError',
    'build_template_card',
    'build_template_cards',
    'campaign_purpose_for_recipient_type',
    'compatible_templates_for_recipient_type',
    'resolve_selected_template',
    'template_still_available',
]
