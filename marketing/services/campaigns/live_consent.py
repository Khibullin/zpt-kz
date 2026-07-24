from __future__ import annotations

from typing import TYPE_CHECKING

from core.models import (
    BUYER_CONTACT_STATUS_ACTIVE,
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    BuyerContact,
    ContactConsent,
)
from marketing.services.campaigns.constants import ELIGIBILITY_ELIGIBLE

if TYPE_CHECKING:
    from marketing.models import MarketingCampaignRecipient

SKIP_REASON_CONSENT_REVOKED = 'consent_revoked'
SKIP_REASON_CONSENT_UNKNOWN = 'consent_unknown'
SKIP_REASON_CONSENT_NOT_GRANTED = 'consent_not_granted'
SKIP_REASON_TEST_CONTACT = 'test_contact'
SKIP_REASON_INACTIVE = 'inactive'
SKIP_REASON_INVALID_PHONE = 'invalid_phone'
SKIP_REASON_PURPOSE_MISMATCH = 'purpose_mismatch'


def get_buyer_contact_for_phone(phone_normalized: str) -> BuyerContact | None:
    return BuyerContact.objects.filter(phone_normalized=phone_normalized).first()


def get_live_marketing_consent_status(buyer: BuyerContact) -> str:
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
        return ''
    return consent.status


def _consent_skip_reason(consent_status: str) -> str:
    if consent_status == CONTACT_CONSENT_STATUS_REVOKED:
        return SKIP_REASON_CONSENT_REVOKED
    if consent_status == CONTACT_CONSENT_STATUS_UNKNOWN:
        return SKIP_REASON_CONSENT_UNKNOWN
    return SKIP_REASON_CONSENT_NOT_GRANTED


def evaluate_live_recipient_from_snapshot(recipient: MarketingCampaignRecipient) -> tuple[bool, str]:
    if recipient.is_test_contact:
        return False, SKIP_REASON_TEST_CONTACT
    if recipient.eligibility_status != ELIGIBILITY_ELIGIBLE:
        reason = recipient.exclusion_reason or SKIP_REASON_PURPOSE_MISMATCH
        return False, reason
    if recipient.consent_status == CONTACT_CONSENT_STATUS_REVOKED:
        return False, SKIP_REASON_CONSENT_REVOKED
    if recipient.consent_status == CONTACT_CONSENT_STATUS_UNKNOWN:
        return False, SKIP_REASON_CONSENT_UNKNOWN
    if recipient.consent_status != CONTACT_CONSENT_STATUS_GRANTED:
        return False, SKIP_REASON_CONSENT_NOT_GRANTED
    return True, ''


def recheck_live_recipient_consent(recipient: MarketingCampaignRecipient) -> tuple[bool, str]:
    if recipient.is_test_contact:
        return False, SKIP_REASON_TEST_CONTACT
    buyer = get_buyer_contact_for_phone(recipient.phone_normalized)
    if buyer is None:
        return False, SKIP_REASON_CONSENT_NOT_GRANTED
    if buyer.is_test_contact:
        return False, SKIP_REASON_TEST_CONTACT
    if buyer.status != BUYER_CONTACT_STATUS_ACTIVE:
        return False, SKIP_REASON_INACTIVE
    consent_status = get_live_marketing_consent_status(buyer)
    if consent_status != CONTACT_CONSENT_STATUS_GRANTED:
        return False, _consent_skip_reason(consent_status)
    return True, ''
