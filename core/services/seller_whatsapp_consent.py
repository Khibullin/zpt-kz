from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from urllib.parse import urljoin

from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.models import (
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_SOURCE_REGISTRATION,
    CONTACT_CONSENT_SOURCE_SELLER_PORTAL,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    SELLER_LINK_WHATSAPP_CONSENT_VERSION,
    SELLER_PORTAL_WHATSAPP_CONSENT_VERSION,
    SELLER_REGISTRATION_WHATSAPP_CONSENT_VERSION,
    Seller,
    SellerContactConsent,
)
from core.phone_utils import normalize_kz_phone

logger = logging.getLogger(__name__)

CONSENT_LINK_SALT = 'core.seller-whatsapp-consent'
CONSENT_LINK_MAX_AGE_SECONDS = 30 * 24 * 60 * 60

CONSENT_ACTION_GRANT = 'grant'
CONSENT_ACTION_REVOKE = 'revoke'
USED_OR_STALE_LINK_MESSAGE = 'Эта ссылка уже использована или устарела.'


class SellerWhatsAppConsentError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class SellerWhatsAppConsentTokenExpired(SellerWhatsAppConsentError):
    def __init__(self):
        super().__init__('Ссылка подтверждения устарела.')


class SellerWhatsAppConsentTokenInvalid(SellerWhatsAppConsentError):
    def __init__(self):
        super().__init__('Ссылка недействительна.')


class SellerWhatsAppConsentTokenStale(SellerWhatsAppConsentError):
    def __init__(self):
        super().__init__(USED_OR_STALE_LINK_MESSAGE)


@dataclass(frozen=True)
class SellerWhatsAppConsentLink:
    seller: Seller
    issued_at: datetime


def _consent_signer() -> TimestampSigner:
    return TimestampSigner(salt=CONSENT_LINK_SALT)


def is_whatsapp_marketing_opt_in(value: object) -> bool:
    if value is True or value == 1:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return False


def get_seller_whatsapp_marketing_consent(seller: Seller) -> SellerContactConsent | None:
    phone = normalize_kz_phone(getattr(seller, 'whatsapp', ''))
    if not phone:
        return None
    return (
        SellerContactConsent.objects.filter(
            seller=seller,
            phone_normalized=phone,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        )
        .order_by('-updated_at', '-id')
        .first()
    )


def get_seller_whatsapp_marketing_consent_status(seller: Seller) -> str:
    consent = get_seller_whatsapp_marketing_consent(seller)
    if consent is None:
        return ''
    return consent.status


def set_seller_whatsapp_marketing_consent(
    seller: Seller,
    granted: bool,
    *,
    source: str,
    consent_text_version: str,
    evidence_reference: str,
) -> SellerContactConsent:
    with transaction.atomic():
        locked_seller = Seller.objects.select_for_update().get(pk=seller.pk)
        phone = normalize_kz_phone(locked_seller.whatsapp)
        if not phone:
            raise SellerWhatsAppConsentError(
                'Некорректный номер WhatsApp для записи согласия.'
            )
        now = timezone.now()
        consent = (
            SellerContactConsent.objects.select_for_update()
            .filter(
                seller=locked_seller,
                phone_normalized=phone,
                channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
                purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            )
            .order_by('-updated_at', '-id')
            .first()
        )
        if consent is None:
            consent = SellerContactConsent(
                seller=locked_seller,
                phone_normalized=phone,
                channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
                purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            )
        consent.phone_normalized = phone
        consent.source = source
        consent.consent_text_version = consent_text_version
        consent.evidence_reference = evidence_reference
        if granted:
            consent.status = CONTACT_CONSENT_STATUS_GRANTED
            consent.consented_at = now
            consent.revoked_at = None
            locked_seller.receive_requests = True
            locked_seller.is_paused = False
        else:
            consent.status = CONTACT_CONSENT_STATUS_REVOKED
            consent.revoked_at = now
            locked_seller.receive_requests = False
            locked_seller.is_paused = True
        consent.save()
        locked_seller.save(update_fields=['receive_requests', 'is_paused'])
        seller.receive_requests = locked_seller.receive_requests
        seller.is_paused = locked_seller.is_paused
        return consent


def maybe_grant_registration_whatsapp_consent(seller: Seller) -> SellerContactConsent | None:
    try:
        return set_seller_whatsapp_marketing_consent(
            seller,
            True,
            source=CONTACT_CONSENT_SOURCE_REGISTRATION,
            consent_text_version=SELLER_REGISTRATION_WHATSAPP_CONSENT_VERSION,
            evidence_reference=f'registration:seller:{seller.pk}',
        )
    except SellerWhatsAppConsentError:
        logger.warning(
            'seller registration whatsapp consent skipped seller_id=%s',
            seller.pk,
        )
        return None


def apply_seller_portal_whatsapp_consent(
    seller: Seller,
    *,
    granted: bool,
) -> SellerContactConsent:
    return set_seller_whatsapp_marketing_consent(
        seller,
        granted,
        source=CONTACT_CONSENT_SOURCE_SELLER_PORTAL,
        consent_text_version=SELLER_PORTAL_WHATSAPP_CONSENT_VERSION,
        evidence_reference=f'seller_portal:authenticated:seller:{seller.pk}',
    )


def apply_seller_link_whatsapp_consent(
    seller: Seller,
    *,
    granted: bool,
    issued_at: datetime,
) -> SellerContactConsent:
    with transaction.atomic():
        locked_seller = Seller.objects.select_for_update().get(pk=seller.pk)
        phone = normalize_kz_phone(locked_seller.whatsapp)
        if not phone:
            raise SellerWhatsAppConsentError(
                'Некорректный номер WhatsApp для записи согласия.'
            )
        consent = (
            SellerContactConsent.objects.select_for_update()
            .filter(
                seller=locked_seller,
                phone_normalized=phone,
                channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
                purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            )
            .order_by('-updated_at', '-id')
            .first()
        )
        if _consent_is_newer_than_link(consent, issued_at):
            raise SellerWhatsAppConsentTokenStale()
        return set_seller_whatsapp_marketing_consent(
            locked_seller,
            granted,
            source=CONTACT_CONSENT_SOURCE_SELLER_PORTAL,
            consent_text_version=SELLER_LINK_WHATSAPP_CONSENT_VERSION,
            evidence_reference=f'seller_portal:signed_link:seller:{locked_seller.pk}',
        )


def _parse_issued_at(value: object) -> datetime:
    try:
        issued_ts = float(value)
    except (TypeError, ValueError) as exc:
        raise SellerWhatsAppConsentTokenInvalid() from exc
    if issued_ts <= 0:
        raise SellerWhatsAppConsentTokenInvalid()
    try:
        issued_at = datetime.fromtimestamp(issued_ts, tz=dt_timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise SellerWhatsAppConsentTokenInvalid() from exc
    return issued_at


def _aware_datetime(value: datetime) -> datetime:
    if timezone.is_naive(value):
        return timezone.make_aware(value, dt_timezone.utc)
    return value


def _consent_is_newer_than_link(
    consent: SellerContactConsent | None,
    issued_at: datetime,
) -> bool:
    if consent is None or not consent.updated_at:
        return False
    return _aware_datetime(consent.updated_at) > _aware_datetime(issued_at)


def is_seller_whatsapp_consent_link_consumed(
    seller: Seller,
    issued_at: datetime,
) -> bool:
    return _consent_is_newer_than_link(
        get_seller_whatsapp_marketing_consent(seller),
        issued_at,
    )


def build_seller_whatsapp_consent_token(seller: Seller) -> str:
    phone = normalize_kz_phone(seller.whatsapp)
    if not phone:
        raise SellerWhatsAppConsentError(
            'Некорректный номер WhatsApp для ссылки подтверждения.'
        )
    return _consent_signer().sign_object({
        'seller_id': int(seller.pk),
        'whatsapp': phone,
        'issued_at': timezone.now().timestamp(),
    })


def resolve_seller_from_whatsapp_consent_token(token: str) -> SellerWhatsAppConsentLink:
    raw = str(token or '').strip()
    if not raw:
        raise SellerWhatsAppConsentTokenInvalid()
    try:
        payload = _consent_signer().unsign_object(
            raw,
            max_age=CONSENT_LINK_MAX_AGE_SECONDS,
        )
    except SignatureExpired as exc:
        raise SellerWhatsAppConsentTokenExpired() from exc
    except (BadSignature, TypeError, ValueError) as exc:
        raise SellerWhatsAppConsentTokenInvalid() from exc
    if not isinstance(payload, dict):
        raise SellerWhatsAppConsentTokenInvalid()
    try:
        seller_id = int(payload.get('seller_id'))
    except (TypeError, ValueError) as exc:
        raise SellerWhatsAppConsentTokenInvalid() from exc
    token_phone = normalize_kz_phone(payload.get('whatsapp'))
    if not token_phone:
        raise SellerWhatsAppConsentTokenInvalid()
    issued_at = _parse_issued_at(payload.get('issued_at'))
    seller = Seller.objects.filter(pk=seller_id).first()
    if seller is None:
        raise SellerWhatsAppConsentTokenInvalid()
    if not seller.is_active or seller.is_test_seller:
        raise SellerWhatsAppConsentTokenInvalid()
    current_phone = normalize_kz_phone(seller.whatsapp)
    if current_phone != token_phone:
        raise SellerWhatsAppConsentTokenInvalid()
    return SellerWhatsAppConsentLink(seller=seller, issued_at=issued_at)


def build_seller_whatsapp_consent_url(seller: Seller) -> str:
    token = build_seller_whatsapp_consent_token(seller)
    path = reverse('seller_whatsapp_consent_link', kwargs={'token': token})
    base = str(getattr(settings, 'PUBLIC_BASE_URL', '') or 'https://zpt.kz').rstrip('/') + '/'
    return urljoin(base, path.lstrip('/'))


def consent_status_label(status: str) -> str:
    if status == CONTACT_CONSENT_STATUS_GRANTED:
        return 'granted'
    if status == CONTACT_CONSENT_STATUS_REVOKED:
        return 'revoked'
    if status == CONTACT_CONSENT_STATUS_UNKNOWN:
        return 'unknown'
    return ''
