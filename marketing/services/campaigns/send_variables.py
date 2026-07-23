from __future__ import annotations

import os
from typing import TYPE_CHECKING

from django.urls import reverse

from core.buyer_portal import normalize_buyer_phone
from core.models import BuyerPortalAccess
from marketing.services.campaigns.send_constants import (
    FORBIDDEN_SAMPLE_ACCESS_TOKEN,
    VARIABLE_KEY_REQUEST_HISTORY_URL,
)

if TYPE_CHECKING:
    from marketing.models import MarketingCampaignRecipient, MarketingWhatsAppTemplate


class VariableResolutionError(Exception):
    def __init__(self, message: str, *, recipient_phone: str = '') -> None:
        super().__init__(message)
        self.recipient_phone = recipient_phone


def _public_base_url() -> str:
    return os.getenv('PUBLIC_BASE_URL', 'https://zpt.kz').rstrip('/')


def resolve_request_history_url(phone_normalized: str) -> str | None:
    phone = normalize_buyer_phone(phone_normalized)
    if not phone:
        return None
    portals = BuyerPortalAccess.objects.filter(phone_normalized=phone)
    portal_count = portals.count()
    if portal_count == 0:
        return None
    if portal_count > 1:
        raise VariableResolutionError(
            'Неоднозначный BuyerPortalAccess для номера телефона.',
            recipient_phone=phone,
        )
    portal = portals.first()
    if not portal or not portal.access_token:
        return None
    token_str = str(portal.access_token)
    if token_str == FORBIDDEN_SAMPLE_ACCESS_TOKEN:
        return None
    relative = reverse(
        'view_buyer_request_history_public',
        kwargs={'access_token': portal.access_token},
    )
    path = relative if relative.startswith('/') else f'/{relative}'
    return f'{_public_base_url()}{path}'


def resolve_template_variables_for_recipient(
    template: MarketingWhatsAppTemplate,
    recipient: MarketingCampaignRecipient,
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for variable in template.variables or []:
        key = variable.get('key', '')
        if not key:
            continue
        if key == VARIABLE_KEY_REQUEST_HISTORY_URL:
            value = resolve_request_history_url(recipient.phone_normalized)
            if not value:
                label = variable.get('label') or key
                raise VariableResolutionError(
                    f'Не удалось построить {label} для {recipient.masked_phone}.',
                    recipient_phone=recipient.phone_normalized,
                )
            if FORBIDDEN_SAMPLE_ACCESS_TOKEN in value:
                raise VariableResolutionError(
                    f'Недопустимый access token для {recipient.masked_phone}.',
                    recipient_phone=recipient.phone_normalized,
                )
            resolved[key] = value
            continue
        if variable.get('required'):
            label = variable.get('label') or key
            raise VariableResolutionError(
                f'Переменная «{label}» не поддерживается для отправки.',
                recipient_phone=recipient.phone_normalized,
            )
    return resolved
