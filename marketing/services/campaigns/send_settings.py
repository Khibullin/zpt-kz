from __future__ import annotations

import os

from django.conf import settings

MARKETING_WHATSAPP_SEND_MODE_OFF = 'OFF'
MARKETING_WHATSAPP_SEND_MODE_TEST = 'TEST'

_ALLOWED_MODES = {
    MARKETING_WHATSAPP_SEND_MODE_OFF,
    MARKETING_WHATSAPP_SEND_MODE_TEST,
}

MARKETING_TEST_SEND_MAX_RECIPIENTS = 2


def get_marketing_whatsapp_send_mode() -> str:
    raw_value = os.getenv(
        'MARKETING_WHATSAPP_SEND_MODE',
        getattr(settings, 'MARKETING_WHATSAPP_SEND_MODE', MARKETING_WHATSAPP_SEND_MODE_OFF),
    )
    normalized = str(raw_value or '').strip().upper()
    if normalized not in _ALLOWED_MODES:
        return MARKETING_WHATSAPP_SEND_MODE_OFF
    return normalized


def marketing_test_whatsapp_send_enabled() -> bool:
    return get_marketing_whatsapp_send_mode() == MARKETING_WHATSAPP_SEND_MODE_TEST
