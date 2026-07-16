from __future__ import annotations

import os

from django.conf import settings

BUYER_BROADCAST_MODE_OFF = 'OFF'
BUYER_BROADCAST_MODE_TEST = 'TEST'

_ALLOWED_MODES = {
    BUYER_BROADCAST_MODE_OFF,
    BUYER_BROADCAST_MODE_TEST,
}


def get_buyer_broadcast_mode() -> str:
    raw_value = os.getenv(
        'BUYER_BROADCAST_MODE',
        getattr(settings, 'BUYER_BROADCAST_MODE', BUYER_BROADCAST_MODE_OFF),
    )
    normalized = str(raw_value or '').strip().upper()
    if normalized not in _ALLOWED_MODES:
        return BUYER_BROADCAST_MODE_OFF
    return normalized


def buyer_test_broadcast_enabled() -> bool:
    return get_buyer_broadcast_mode() == BUYER_BROADCAST_MODE_TEST


def get_buyer_broadcast_test_max_recipients() -> int:
    raw_value = os.getenv(
        'BUYER_BROADCAST_TEST_MAX_RECIPIENTS',
        getattr(settings, 'BUYER_BROADCAST_TEST_MAX_RECIPIENTS', 5),
    )
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return 5
    if parsed <= 0:
        return 5
    return parsed
