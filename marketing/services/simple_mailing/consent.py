from __future__ import annotations

from core.models import (
    BUYER_CONTACT_STATUS_ACTIVE,
    BUYER_CONTACT_STATUS_BLOCKED,
    BUYER_CONTACT_STATUS_INVALID_PHONE,
    BUYER_CONTACT_STATUS_UNSUBSCRIBED,
    BUYER_CONTACT_STATUS_WHATSAPP_UNAVAILABLE,
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    BuyerContact,
    ContactConsent,
    Seller,
)
from marketing.services.phone_utils import normalize_phone_key

SKIP_REASON_CONSENT_REVOKED = 'consent_revoked'
SKIP_REASON_CONSENT_UNKNOWN = 'consent_unknown'
SKIP_REASON_TEST_CONTACT = 'test_contact'
SKIP_REASON_INACTIVE = 'inactive'
SKIP_REASON_INVALID_PHONE = 'invalid_phone'
SKIP_REASON_UNSUBSCRIBED = 'unsubscribed'
SKIP_REASON_BLOCKED = 'blocked'

_HARD_BLOCKED_STATUSES = frozenset({
    BUYER_CONTACT_STATUS_INVALID_PHONE,
    BUYER_CONTACT_STATUS_WHATSAPP_UNAVAILABLE,
    BUYER_CONTACT_STATUS_UNSUBSCRIBED,
    BUYER_CONTACT_STATUS_BLOCKED,
})


def _marketing_consent_status(buyer: BuyerContact) -> str:
    consent = (
        ContactConsent.objects.filter(
            buyer=buyer,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        )
        .order_by('-updated_at', '-id')
        .first()
    )
    if consent is None:
        return CONTACT_CONSENT_STATUS_UNKNOWN
    return consent.status


def _hard_exclusion_reason(buyer: BuyerContact) -> str:
    if buyer.status == BUYER_CONTACT_STATUS_INVALID_PHONE:
        return SKIP_REASON_INVALID_PHONE
    if buyer.status == BUYER_CONTACT_STATUS_UNSUBSCRIBED:
        return SKIP_REASON_UNSUBSCRIBED
    if buyer.status == BUYER_CONTACT_STATUS_BLOCKED:
        return SKIP_REASON_BLOCKED
    if buyer.status == BUYER_CONTACT_STATUS_WHATSAPP_UNAVAILABLE:
        return SKIP_REASON_INACTIVE
    if buyer.status != BUYER_CONTACT_STATUS_ACTIVE:
        return SKIP_REASON_INACTIVE
    if _marketing_consent_status(buyer) == CONTACT_CONSENT_STATUS_REVOKED:
        return SKIP_REASON_CONSENT_REVOKED
    return ''


def evaluate_simple_mailing_phone(
    *,
    phone_normalized: str,
    is_test: bool = False,
    is_control: bool = False,
) -> tuple[bool, str]:
    phone = normalize_phone_key(phone_normalized)
    if not phone:
        return False, SKIP_REASON_INVALID_PHONE
    if is_test and not is_control:
        return False, SKIP_REASON_TEST_CONTACT

    buyer = BuyerContact.objects.filter(phone_normalized=phone).first()
    if buyer is None:
        return True, ''
    if buyer.is_test_contact and not is_control:
        return False, SKIP_REASON_TEST_CONTACT

    hard_reason = _hard_exclusion_reason(buyer)
    if hard_reason:
        return False, hard_reason

    consent_status = _marketing_consent_status(buyer)
    if consent_status == CONTACT_CONSENT_STATUS_REVOKED:
        return False, SKIP_REASON_CONSENT_REVOKED
    if consent_status in {CONTACT_CONSENT_STATUS_GRANTED, CONTACT_CONSENT_STATUS_UNKNOWN, ''}:
        return True, ''
    return False, SKIP_REASON_CONSENT_REVOKED


def recheck_simple_mailing_phone(*, phone_normalized: str) -> tuple[bool, str]:
    return evaluate_simple_mailing_phone(phone_normalized=phone_normalized)


def recheck_simple_mailing_recipient(recipient) -> tuple[bool, str]:
    phone = normalize_phone_key(recipient.phone_normalized)
    if not phone:
        return False, SKIP_REASON_INVALID_PHONE
    is_control = bool(getattr(recipient, 'is_control_recipient', False))
    if recipient.is_test_contact and not is_control:
        return False, SKIP_REASON_TEST_CONTACT
    if _is_test_seller_phone(phone):
        return False, SKIP_REASON_TEST_CONTACT
    return evaluate_simple_mailing_phone(
        phone_normalized=phone,
        is_test=recipient.is_test_contact,
        is_control=is_control,
    )


def _is_test_seller_phone(phone: str) -> bool:
    for seller in Seller.objects.filter(is_test_seller=True).only('whatsapp', 'phone2'):
        if normalize_phone_key(seller.whatsapp) == phone:
            return True
        if normalize_phone_key(seller.phone2) == phone:
            return True
    return False
