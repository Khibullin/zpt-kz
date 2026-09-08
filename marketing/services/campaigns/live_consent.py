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
    SellerContactConsent,
)
from core.phone_utils import normalize_kz_phone
from core.services.seller_identity import find_sellers_by_phone
from marketing.services.campaigns.constants import (
    ELIGIBILITY_ELIGIBLE,
    PURPOSE_ALL_SELLERS,
    PURPOSE_COMBINED_SELLERS,
    PURPOSE_MARKETPLACE_SELLERS,
    PURPOSE_REQUEST_SELLERS,
)

if TYPE_CHECKING:
    from marketing.models import MarketingCampaignRecipient

SKIP_REASON_CONSENT_REVOKED = 'consent_revoked'
SKIP_REASON_CONSENT_UNKNOWN = 'consent_unknown'
SKIP_REASON_CONSENT_NOT_GRANTED = 'consent_not_granted'
SKIP_REASON_TEST_CONTACT = 'test_contact'
SKIP_REASON_INACTIVE = 'inactive'
SKIP_REASON_INVALID_PHONE = 'invalid_phone'
SKIP_REASON_PURPOSE_MISMATCH = 'purpose_mismatch'
SKIP_REASON_SELLER_NOT_RECEIVING = 'seller_not_receiving'

SELLER_CAMPAIGN_PURPOSES = frozenset({
    PURPOSE_REQUEST_SELLERS,
    PURPOSE_MARKETPLACE_SELLERS,
    PURPOSE_COMBINED_SELLERS,
    PURPOSE_ALL_SELLERS,
})


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


def get_seller_marketing_consent_status(seller, phone_normalized: str) -> str:
    consent = (
        SellerContactConsent.objects.filter(
            seller=seller,
            phone_normalized=phone_normalized,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        )
        .order_by('-updated_at', '-id')
        .first()
    )
    if consent is None:
        return ''
    return consent.status


def is_seller_campaign_purpose(purpose: str) -> bool:
    return purpose in SELLER_CAMPAIGN_PURPOSES


def uses_seller_live_consent(*, purpose: str, is_control_recipient: bool = False) -> bool:
    return is_seller_campaign_purpose(purpose) and not is_control_recipient


def seller_consent_status_for_phone(phone_normalized: str) -> str:
    phone = normalize_kz_phone(phone_normalized)
    if not phone:
        return ''
    sellers = find_sellers_by_phone(phone)
    if len(sellers) != 1:
        return ''
    return get_seller_marketing_consent_status(sellers[0], phone)


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
    purpose = getattr(getattr(recipient, 'campaign', None), 'purpose', '') or ''
    if is_seller_campaign_purpose(purpose):
        return _recheck_seller_recipient_consent(recipient, purpose=purpose)
    return _recheck_buyer_recipient_consent(recipient)


def _recheck_buyer_recipient_consent(recipient: MarketingCampaignRecipient) -> tuple[bool, str]:
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


def _recheck_seller_recipient_consent(
    recipient: MarketingCampaignRecipient,
    *,
    purpose: str,
) -> tuple[bool, str]:
    phone = normalize_kz_phone(recipient.phone_normalized)
    if not phone:
        return False, SKIP_REASON_INVALID_PHONE
    sellers = find_sellers_by_phone(phone)
    if len(sellers) != 1:
        return False, SKIP_REASON_CONSENT_NOT_GRANTED
    seller = sellers[0]
    if seller.is_test_seller:
        return False, SKIP_REASON_TEST_CONTACT
    consent_status = get_seller_marketing_consent_status(seller, phone)
    if consent_status != CONTACT_CONSENT_STATUS_GRANTED:
        return False, _consent_skip_reason(consent_status)
    if not seller.is_active:
        return False, SKIP_REASON_INACTIVE
    if purpose == PURPOSE_REQUEST_SELLERS:
        if not seller.receive_requests or seller.is_paused:
            return False, SKIP_REASON_SELLER_NOT_RECEIVING
    return True, ''
