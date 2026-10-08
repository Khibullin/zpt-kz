from __future__ import annotations

from django.conf import settings
from django.core import signing
from django.urls import reverse
from django.utils import timezone

from core.models import Seller, SellerLead, normalize_seller_lead_whatsapp
from core.phone_utils import build_whatsapp_url
from core.services.seller_lead_admin_workflow import compute_review_status


SELLER_INVITE_SIGNING_SALT = 'seller-marketplace-invite-v1'
SELLER_INVITE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30


def build_seller_invite_token(lead: SellerLead) -> str:
    """Signed, URL-safe invite token containing only the SellerLead id."""
    return signing.dumps(
        {'lead_id': lead.pk},
        salt=SELLER_INVITE_SIGNING_SALT,
        compress=True,
    )


def decode_seller_invite_token(token: str) -> int:
    payload = signing.loads(
        token,
        salt=SELLER_INVITE_SIGNING_SALT,
        max_age=SELLER_INVITE_MAX_AGE_SECONDS,
    )
    lead_id = payload.get('lead_id')
    if not isinstance(lead_id, int) or lead_id <= 0:
        raise signing.BadSignature('Invalid seller invite lead id')
    return lead_id


def build_marketplace_registration_url(
    lead: SellerLead,
    *,
    base_url: str | None = None,
) -> str:
    """Short public seller invite URL; lead details stay server-side."""
    token = build_seller_invite_token(lead)
    relative = reverse('seller_join', kwargs={'token': token})
    public_base = (base_url or getattr(settings, 'PUBLIC_BASE_URL', 'https://zpt.kz')).rstrip('/')
    return f'{public_base}{relative}'


def build_marketplace_invite_message(lead: SellerLead) -> str:
    registration_url = build_marketplace_registration_url(lead)
    name = (lead.name or '').strip()
    greeting = f'Здравствуйте! Приглашаем {name} подключиться к ZPT.KZ.' if name else (
        'Здравствуйте! Приглашаем ваш магазин подключиться к ZPT.KZ.'
    )
    return (
        f'{greeting}\n\n'
        'Можно создать кабинет продавца, разместить товары и работать с заявками покупателей.\n'
        f'Регистрация: {registration_url}'
    )


def build_marketplace_invite_whatsapp_url(lead: SellerLead) -> str:
    """Manual outreach link. This function never sends a WhatsApp message."""
    return build_whatsapp_url(
        lead.whatsapp,
        text=build_marketplace_invite_message(lead),
    )


def mark_seller_lead_invited(lead: SellerLead) -> bool:
    """Record a manual marketplace invitation after the operator sends it.

    This function does not send WhatsApp. It only moves a ready lead to
    lifecycle=invited and keeps the legacy marketplace-planned fields aligned.
    """
    if lead.lifecycle_status == SellerLead.LIFECYCLE_INVITED:
        return False
    if lead.lifecycle_status != SellerLead.LIFECYCLE_READY_TO_INVITE:
        return False
    if not build_marketplace_invite_whatsapp_url(lead):
        return False

    now = timezone.now()
    lead.lifecycle_status = SellerLead.LIFECYCLE_INVITED
    update_fields = {'lifecycle_status', 'updated_at'}

    if (
        lead.marketplace_invitation_status
        != SellerLead.MARKETPLACE_INVITATION_PLANNED
    ):
        lead.marketplace_invitation_status = (
            SellerLead.MARKETPLACE_INVITATION_PLANNED
        )
        update_fields.add('marketplace_invitation_status')

    if lead.marketplace_invitation_planned_at is None:
        lead.marketplace_invitation_planned_at = now
        update_fields.add('marketplace_invitation_planned_at')

    if lead.reviewed_at is None:
        lead.reviewed_at = now
        update_fields.add('reviewed_at')

    review_status = compute_review_status(lead)
    if lead.review_status != review_status:
        lead.review_status = review_status
        update_fields.add('review_status')

    lead.save(update_fields=sorted(update_fields))
    return True


def claim_seller_lead_after_registration(
    *,
    phone: str,
    request_seller: Seller,
) -> SellerLead | None:
    """Close the discovery loop when the discovered merchant self-registers.

    The match is intentionally phone-only and exact after SellerLead normalization.
    A conflicting existing request_seller is never overwritten.
    """
    normalized = normalize_seller_lead_whatsapp(phone)
    if not normalized:
        return None

    lead = (
        SellerLead.objects
        .filter(whatsapp=normalized, duplicate_of__isnull=True)
        .exclude(
            lifecycle_status__in=(
                SellerLead.LIFECYCLE_DUPLICATE,
                SellerLead.LIFECYCLE_REJECTED,
                SellerLead.LIFECYCLE_CLOSED,
            )
        )
        .first()
    )
    if lead is None:
        return None
    if lead.request_seller_id and lead.request_seller_id != request_seller.pk:
        return None

    now = timezone.now()
    update_fields: set[str] = {'updated_at'}

    if lead.request_seller_id != request_seller.pk:
        lead.request_seller = request_seller
        update_fields.add('request_seller')

    if lead.status != SellerLead.STATUS_REGISTERED:
        lead.status = SellerLead.STATUS_REGISTERED
        update_fields.add('status')

    if lead.lifecycle_status not in (
        SellerLead.LIFECYCLE_VERIFIED,
        SellerLead.LIFECYCLE_ACTIVE,
    ):
        if lead.lifecycle_status != SellerLead.LIFECYCLE_CLAIMED:
            lead.lifecycle_status = SellerLead.LIFECYCLE_CLAIMED
            update_fields.add('lifecycle_status')

    if lead.reviewed_at is None:
        lead.reviewed_at = now
        update_fields.add('reviewed_at')

    review_status = compute_review_status(lead)
    if lead.review_status != review_status:
        lead.review_status = review_status
        update_fields.add('review_status')

    lead.save(update_fields=sorted(update_fields))
    return lead
