from __future__ import annotations

import os

from django.conf import settings

MARKETING_WHATSAPP_SEND_MODE_OFF = 'OFF'
MARKETING_WHATSAPP_SEND_MODE_TEST = 'TEST'
MARKETING_WHATSAPP_SEND_MODE_LIVE = 'LIVE'

_ALLOWED_MODES = {
    MARKETING_WHATSAPP_SEND_MODE_OFF,
    MARKETING_WHATSAPP_SEND_MODE_TEST,
    MARKETING_WHATSAPP_SEND_MODE_LIVE,
}

MARKETING_TEST_SEND_MAX_RECIPIENTS = 2

MARKETING_LIVE_BATCH_SIZE_DEFAULT = 10
MARKETING_LIVE_MAX_RECIPIENTS_DEFAULT = 10
MARKETING_LIVE_SEND_INTERVAL_SECONDS_DEFAULT = 2

MARKETING_LIVE_INT_MIN = 1
MARKETING_LIVE_INT_MAX = 100
MARKETING_LIVE_INTERVAL_MIN = 1
MARKETING_LIVE_INTERVAL_MAX = 60


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


def marketing_live_whatsapp_send_enabled() -> bool:
    return get_marketing_whatsapp_send_mode() == MARKETING_WHATSAPP_SEND_MODE_LIVE


def _read_bounded_int(
    env_name: str,
    setting_name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw_value = os.getenv(env_name, getattr(settings, setting_name, default))
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum or parsed > maximum:
        return default
    return parsed


def get_marketing_live_batch_size() -> int:
    return _read_bounded_int(
        'MARKETING_LIVE_BATCH_SIZE',
        'MARKETING_LIVE_BATCH_SIZE',
        MARKETING_LIVE_BATCH_SIZE_DEFAULT,
        minimum=MARKETING_LIVE_INT_MIN,
        maximum=MARKETING_LIVE_INT_MAX,
    )


def get_marketing_live_max_recipients() -> int:
    return _read_bounded_int(
        'MARKETING_LIVE_MAX_RECIPIENTS',
        'MARKETING_LIVE_MAX_RECIPIENTS',
        MARKETING_LIVE_MAX_RECIPIENTS_DEFAULT,
        minimum=MARKETING_LIVE_INT_MIN,
        maximum=MARKETING_LIVE_INT_MAX,
    )


def get_marketing_live_send_interval_seconds() -> int:
    return _read_bounded_int(
        'MARKETING_LIVE_SEND_INTERVAL_SECONDS',
        'MARKETING_LIVE_SEND_INTERVAL_SECONDS',
        MARKETING_LIVE_SEND_INTERVAL_SECONDS_DEFAULT,
        minimum=MARKETING_LIVE_INTERVAL_MIN,
        maximum=MARKETING_LIVE_INTERVAL_MAX,
    )
