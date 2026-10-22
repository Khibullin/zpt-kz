"""Separate consent for future buyer requests; never grants promotional opt-in."""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from core.models import (
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_BUYER_REQUESTS,
    CONTACT_CONSENT_SOURCE_SELLER_REQUEST_PAGE,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    Seller,
    SellerContactConsent,
)
from core.phone_utils import normalize_kz_phone

CONSENT_TEXT_VERSION = 'seller_buyer_requests_v1'


def get_request_consent(seller):
    phone = normalize_kz_phone(getattr(seller, 'whatsapp', ''))
    if not phone:
        return None
    return SellerContactConsent.objects.filter(
        seller=seller,
        phone_normalized=phone,
        channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
        purpose=CONTACT_CONSENT_PURPOSE_BUYER_REQUESTS,
    ).order_by('-updated_at', '-id').first()


def request_consent_status(seller) -> str:
    consent = get_request_consent(seller)
    return consent.status if consent else ''


@transaction.atomic
def save_request_consent(seller, *, granted: bool, evidence_reference: str):
    """Store independent, revocable WhatsApp buyer-request subscription.

    Does not change the marketing consent or the seller's active/paused flags.
    The seller may change their choice on a later valid personal request page.
    """
    locked = Seller.objects.select_for_update().get(pk=seller.pk)
    phone = normalize_kz_phone(locked.whatsapp)
    if not phone:
        raise ValueError('Некорректный номер WhatsApp продавца.')
    consent = (
        SellerContactConsent.objects.select_for_update()
        .filter(
            seller=locked,
            phone_normalized=phone,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_BUYER_REQUESTS,
        ).first()
    )
    if consent is None:
        consent = SellerContactConsent(
            seller=locked,
            phone_normalized=phone,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_BUYER_REQUESTS,
        )
    now = timezone.now()
    consent.source = CONTACT_CONSENT_SOURCE_SELLER_REQUEST_PAGE
    consent.consent_text_version = CONSENT_TEXT_VERSION
    consent.evidence_reference = str(evidence_reference)[:255]
    consent.status = CONTACT_CONSENT_STATUS_GRANTED if granted else CONTACT_CONSENT_STATUS_REVOKED
    consent.consented_at = now if granted else consent.consented_at
    consent.revoked_at = None if granted else now
    consent.save()

    if locked.receive_requests != granted:
        locked.receive_requests = granted
        locked.save(update_fields=['receive_requests'])
    seller.receive_requests = locked.receive_requests
    return consent
