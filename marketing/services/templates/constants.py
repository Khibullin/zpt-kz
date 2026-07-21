from __future__ import annotations

from marketing.services.campaigns.constants import (
    CAMPAIGN_PURPOSE_CHOICES,
    PURPOSE_DETAILING_PROVIDERS,
    PURPOSE_COMBINED_SELLERS,
    PURPOSE_MARKETPLACE_BUYERS,
    PURPOSE_MARKETPLACE_SELLERS,
    PURPOSE_PARTS_BUYERS,
    PURPOSE_REQUEST_SELLERS,
    PURPOSE_SERVICE_CUSTOMERS,
    PURPOSE_STO_PROVIDERS,
    PURPOSE_TEST_CAMPAIGN,
)

CATEGORY_MARKETING = 'marketing'

META_STATUS_UNKNOWN = 'unknown'
META_STATUS_DRAFT = 'draft'
META_STATUS_PENDING = 'pending'
META_STATUS_APPROVED = 'approved'
META_STATUS_REJECTED = 'rejected'
META_STATUS_PAUSED = 'paused'
META_STATUS_DISABLED = 'disabled'

META_STATUS_CHOICES = (
    (META_STATUS_UNKNOWN, 'Неизвестен'),
    (META_STATUS_DRAFT, 'Черновик'),
    (META_STATUS_PENDING, 'На проверке'),
    (META_STATUS_APPROVED, 'Одобрен'),
    (META_STATUS_REJECTED, 'Отклонён'),
    (META_STATUS_PAUSED, 'Приостановлен'),
    (META_STATUS_DISABLED, 'Отключён'),
)

USABLE_META_STATUSES = frozenset({META_STATUS_APPROVED})

TEMPLATE_LIST_PAGE_SIZE = 25

MAX_VARIABLES = 20
MAX_BUTTONS = 3
MAX_VARIABLE_KEY_LENGTH = 50
MAX_VARIABLE_LABEL_LENGTH = 120
MAX_VARIABLE_EXAMPLE_LENGTH = 200
MAX_BUTTON_TEXT_LENGTH = 120
MAX_BUTTON_VALUE_LENGTH = 500

TEMPLATE_BUSINESS_PURPOSE_CODES = frozenset({
    PURPOSE_PARTS_BUYERS,
    PURPOSE_MARKETPLACE_BUYERS,
    PURPOSE_SERVICE_CUSTOMERS,
    PURPOSE_REQUEST_SELLERS,
    PURPOSE_MARKETPLACE_SELLERS,
    PURPOSE_COMBINED_SELLERS,
    PURPOSE_STO_PROVIDERS,
    PURPOSE_DETAILING_PROVIDERS,
})

TEMPLATE_BUSINESS_PURPOSE_CHOICES = tuple(
    (code, label)
    for code, label in CAMPAIGN_PURPOSE_CHOICES
    if code in TEMPLATE_BUSINESS_PURPOSE_CODES
)

BUTTON_TYPE_URL = 'url'
BUTTON_TYPE_QUICK_REPLY = 'quick_reply'
BUTTON_TYPE_PHONE = 'phone'

BUTTON_TYPE_CHOICES = (
    (BUTTON_TYPE_URL, 'URL'),
    (BUTTON_TYPE_QUICK_REPLY, 'Быстрый ответ'),
    (BUTTON_TYPE_PHONE, 'Телефон'),
)

FORBIDDEN_VARIABLE_KEYS = frozenset({
    'phone',
    'token',
    'access_token',
    'provider_response',
})

FORBIDDEN_VARIABLE_EXTRA_FIELDS = frozenset({
    'phone',
    'token',
    'access_token',
    'provider_response',
})

BASE_RESERVED_SERVICE_TEMPLATE_NAMES = frozenset({
    'zpt_buyer_request_receipt',
    'zpt_request_notification',
    'hello_world',
    'mp_request_v1',
})


def get_reserved_service_template_names() -> frozenset[str]:
    """Centralized reserved Meta template names (static + from Django settings)."""
    from django.conf import settings

    names = set(BASE_RESERVED_SERVICE_TEMPLATE_NAMES)
    for setting_name in ('WHATSAPP_TEMPLATE_NAME', 'WHATSAPP_BUYER_TEMPLATE_NAME'):
        raw = getattr(settings, setting_name, '') or ''
        normalized = raw.strip().lower()
        if normalized:
            names.add(normalized)
    return frozenset(names)


# Backward-compatible alias for tests and imports.
RESERVED_SERVICE_TEMPLATE_NAMES = BASE_RESERVED_SERVICE_TEMPLATE_NAMES
