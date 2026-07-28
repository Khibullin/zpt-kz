from __future__ import annotations

from core.models import (
    BUYER_CONTACT_STATUS_ACTIVE,
    BUYER_CONTACT_STATUS_BLOCKED,
    BUYER_CONTACT_STATUS_INVALID_PHONE,
    BUYER_CONTACT_STATUS_UNSUBSCRIBED,
    BUYER_CONTACT_STATUS_WHATSAPP_UNAVAILABLE,
    CONTACT_CONSENT_STATUS_REVOKED,
    BuyerContact,
)
from marketing.services.phone_utils import normalize_phone_key
from marketing.services.simple_mailing.consent import _marketing_consent_status
from marketing.services.simple_mailing.launch_recipients import SimpleMailingLaunchRecipient


def control_buyer_passes_hard_safety(buyer: BuyerContact) -> bool:
    phone = normalize_phone_key(buyer.phone_normalized)
    if not phone:
        return False
    if buyer.status in {
        BUYER_CONTACT_STATUS_INVALID_PHONE,
        BUYER_CONTACT_STATUS_WHATSAPP_UNAVAILABLE,
        BUYER_CONTACT_STATUS_UNSUBSCRIBED,
        BUYER_CONTACT_STATUS_BLOCKED,
    }:
        return False
    if buyer.status != BUYER_CONTACT_STATUS_ACTIVE:
        return False
    if _marketing_consent_status(buyer) == CONTACT_CONSENT_STATUS_REVOKED:
        return False
    return True


def list_eligible_control_buyers() -> list[BuyerContact]:
    return [
        buyer
        for buyer in BuyerContact.objects.filter(is_control_recipient=True).order_by(
            'phone_normalized',
            'id',
        )
        if control_buyer_passes_hard_safety(buyer)
    ]


def build_control_launch_recipients() -> list[SimpleMailingLaunchRecipient]:
    rows: list[SimpleMailingLaunchRecipient] = []
    for buyer in list_eligible_control_buyers():
        rows.append(
            SimpleMailingLaunchRecipient(
                phone_normalized=buyer.phone_normalized,
                display_name='—',
                city=buyer.primary_city or '—',
                brands_label='Контрольный',
                is_test_contact=buyer.is_test_contact,
                is_control_recipient=True,
            ),
        )
    return rows


def merge_ordinary_with_controls(
    ordinary: list[SimpleMailingLaunchRecipient],
    controls: list[SimpleMailingLaunchRecipient],
) -> tuple[list[SimpleMailingLaunchRecipient], int]:
    """Return merged recipients and duplicate control count."""
    seen: set[str] = set()
    merged: list[SimpleMailingLaunchRecipient] = []
    for row in ordinary:
        phone = normalize_phone_key(row.phone_normalized)
        if not phone or phone in seen:
            continue
        seen.add(phone)
        merged.append(row)
    duplicate_controls = 0
    for row in controls:
        phone = normalize_phone_key(row.phone_normalized)
        if not phone:
            continue
        if phone in seen:
            duplicate_controls += 1
            continue
        seen.add(phone)
        merged.append(row)
    return merged, duplicate_controls
