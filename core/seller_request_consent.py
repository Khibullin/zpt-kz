from __future__ import annotations

import os

from core.models import CONTACT_CONSENT_STATUS_GRANTED
from core.services.seller_whatsapp_consent import (
    get_seller_whatsapp_marketing_consent_status,
)

SELLER_REQUEST_CONSENT_TEMPLATE = 'zpt_request_notification_consent_v1'
SELLER_REQUEST_CONSENT_YES_TEXT = 'Да, получать'
SELLER_REQUEST_CONSENT_NO_TEXT = 'Отключить'
SELLER_REQUEST_CONSENT_YES_PAYLOAD = 'seller_confirm_yes'
SELLER_REQUEST_CONSENT_NO_PAYLOAD = 'seller_confirm_no'
SELLER_REQUEST_CONSENT_TEMPLATE_ENABLED_ENV = (
    'WHATSAPP_SELLER_CONSENT_TEMPLATE_ENABLED'
)
SELLER_REQUEST_CONSENT_TEMPLATE_NAME_ENV = (
    'WHATSAPP_SELLER_CONSENT_TEMPLATE_NAME'
)


def is_seller_request_consent_template_enabled() -> bool:
    return str(
        os.getenv(SELLER_REQUEST_CONSENT_TEMPLATE_ENABLED_ENV, 'false')
    ).strip().lower() in {'1', 'true', 'yes', 'on'}


def seller_request_consent_template_name() -> str:
    return (
        str(
            os.getenv(
                SELLER_REQUEST_CONSENT_TEMPLATE_NAME_ENV,
                SELLER_REQUEST_CONSENT_TEMPLATE,
            )
        ).strip()
        or SELLER_REQUEST_CONSENT_TEMPLATE
    )


def seller_needs_request_consent_prompt(seller) -> bool:
    return (
        get_seller_whatsapp_marketing_consent_status(seller)
        != CONTACT_CONSENT_STATUS_GRANTED
    )


def seller_request_consent_button_components() -> list[dict]:
    return [
        {
            'type': 'button',
            'sub_type': 'quick_reply',
            'index': '0',
            'parameters': [
                {
                    'type': 'payload',
                    'payload': SELLER_REQUEST_CONSENT_YES_PAYLOAD,
                },
            ],
        },
        {
            'type': 'button',
            'sub_type': 'quick_reply',
            'index': '1',
            'parameters': [
                {
                    'type': 'payload',
                    'payload': SELLER_REQUEST_CONSENT_NO_PAYLOAD,
                },
            ],
        },
    ]


def seller_request_template_kwargs(seller) -> dict:
    """Return opt-in template kwargs only when the approved feature is enabled.

    The feature flag intentionally defaults to false so deploying the code before Meta
    approves the new template cannot interrupt the existing seller request flow.
    """
    if not is_seller_request_consent_template_enabled():
        return {}
    if not seller_needs_request_consent_prompt(seller):
        return {}
    return {
        'template_name': seller_request_consent_template_name(),
        'button_components': seller_request_consent_button_components(),
        'include_image_header': False,
    }
