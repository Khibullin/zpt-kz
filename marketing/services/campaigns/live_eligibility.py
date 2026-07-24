from __future__ import annotations

from django.db.models import Q

from core.models import (
    BUYER_CONTACT_STATUS_ACTIVE,
    BuyerContact,
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
)
from core.services.buyer_audience_service import EXCLUDED_STATUSES
from marketing.services.campaigns.live_consent import get_live_marketing_consent_status


def is_buyer_live_marketing_eligible(buyer: BuyerContact) -> bool:
    if buyer.is_test_contact:
        return False
    if buyer.status != BUYER_CONTACT_STATUS_ACTIVE:
        return False
    if buyer.status in EXCLUDED_STATUSES:
        return False
    phone = buyer.phone_normalized or ''
    if len(phone) != 11 or not phone.isdigit():
        return False
    consent_status = get_live_marketing_consent_status(buyer)
    return consent_status == CONTACT_CONSENT_STATUS_GRANTED


def live_eligible_buyer_q(*, prefix: str = '') -> Q:
    """
    Q-фильтр для аннотаций/агрегаций с теми же правилами, что LIVE send engine.
    """
    field = f'{prefix}' if prefix else ''
    q = Q(**{f'{field}is_test_contact': False})
    q &= Q(**{f'{field}status': BUYER_CONTACT_STATUS_ACTIVE})
    q &= ~Q(**{f'{field}status__in': EXCLUDED_STATUSES})
    q &= Q(
        **{
            f'{field}consents__channel': CONTACT_CONSENT_CHANNEL_WHATSAPP,
            f'{field}consents__purpose': CONTACT_CONSENT_PURPOSE_MARKETING,
            f'{field}consents__status': CONTACT_CONSENT_STATUS_GRANTED,
        },
    )
    return q
