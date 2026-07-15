from __future__ import annotations

from core.models import (
    BUYER_CONTACT_STATUS_BLOCKED,
    BUYER_CONTACT_STATUS_INVALID_PHONE,
    BUYER_CONTACT_STATUS_UNSUBSCRIBED,
    BUYER_CONTACT_STATUS_WHATSAPP_UNAVAILABLE,
    BuyerContact,
)


def eligible_buyer_contacts():
    """
    Это базовый QuerySet для будущего конструктора аудиторий.
    Перед рекламной отправкой дополнительно обязательно проверяется
    marketing consent = granted.
    """
    return BuyerContact.objects.filter(is_test_contact=False).exclude(
        status__in=[
            BUYER_CONTACT_STATUS_INVALID_PHONE,
            BUYER_CONTACT_STATUS_WHATSAPP_UNAVAILABLE,
            BUYER_CONTACT_STATUS_UNSUBSCRIBED,
            BUYER_CONTACT_STATUS_BLOCKED,
        ],
    )
