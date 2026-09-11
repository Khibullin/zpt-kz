"""Secure seller request landing links: /sr/<token>/.

Not wired into WhatsApp send. Tokens are random, unique, and expire.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    SELLER_REQUEST_ACCESS_TOKEN_MAX_LENGTH,
    Request,
    Seller,
    SellerRequestAccess,
)

DEFAULT_TTL = timedelta(hours=2)
TOKEN_BYTES = 24


def generate_seller_request_access_token() -> str:
    token = secrets.token_urlsafe(TOKEN_BYTES)
    return token[:SELLER_REQUEST_ACCESS_TOKEN_MAX_LENGTH]


def create_seller_request_access(
    *,
    request: Request | None = None,
    seller: Seller | None = None,
    ttl: timedelta | None = None,
) -> SellerRequestAccess:
    expires_at = timezone.now() + (ttl or DEFAULT_TTL)
    for _ in range(8):
        token = generate_seller_request_access_token()
        if SellerRequestAccess.objects.filter(token=token).exists():
            continue
        return SellerRequestAccess.objects.create(
            token=token,
            request=request,
            seller=seller,
            expires_at=expires_at,
        )
    raise RuntimeError('Could not allocate a unique seller request access token.')


def find_seller_request_access(token: object) -> SellerRequestAccess | None:
    value = str(token or '').strip()
    if not value or len(value) > SELLER_REQUEST_ACCESS_TOKEN_MAX_LENGTH:
        return None
    return SellerRequestAccess.objects.filter(token=value).first()


def build_seller_request_access_url(access: SellerRequestAccess) -> str:
    path = reverse('seller_request_link', kwargs={'token': access.token})
    base = str(getattr(settings, 'PUBLIC_BASE_URL', 'https://zpt.kz') or 'https://zpt.kz')
    return f'{base.rstrip("/")}{path}'
