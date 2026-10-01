"""Pure identity helpers for Seller Discovery.

No network access and no Seller / User / SellerProfile creation.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

from core.phone_utils import normalize_phone_for_whatsapp

LIFECYCLE_FOUND = 'found'
LIFECYCLE_ENRICHED = 'enriched'
LIFECYCLE_CLASSIFIED = 'classified'
LIFECYCLE_READY_TO_INVITE = 'ready_to_invite'
LIFECYCLE_INVITED = 'invited'
LIFECYCLE_CLAIMED = 'claimed'
LIFECYCLE_VERIFIED = 'verified'
LIFECYCLE_ACTIVE = 'active'
LIFECYCLE_POSSIBLE_DUPLICATE = 'possible_duplicate'
LIFECYCLE_DUPLICATE = 'duplicate'
LIFECYCLE_REJECTED = 'rejected'
LIFECYCLE_UNREACHABLE = 'unreachable'
LIFECYCLE_CLOSED = 'closed'

LIFECYCLE_STATUSES = (
    LIFECYCLE_FOUND,
    LIFECYCLE_ENRICHED,
    LIFECYCLE_CLASSIFIED,
    LIFECYCLE_READY_TO_INVITE,
    LIFECYCLE_INVITED,
    LIFECYCLE_CLAIMED,
    LIFECYCLE_VERIFIED,
    LIFECYCLE_ACTIVE,
    LIFECYCLE_POSSIBLE_DUPLICATE,
    LIFECYCLE_DUPLICATE,
    LIFECYCLE_REJECTED,
    LIFECYCLE_UNREACHABLE,
    LIFECYCLE_CLOSED,
)

# Legacy rows may be moved to POSSIBLE_DUPLICATE only from these states.
MUTABLE_LIFECYCLE_FOR_DUPLICATE_REVIEW = frozenset({
    LIFECYCLE_FOUND,
    LIFECYCLE_ENRICHED,
    LIFECYCLE_CLASSIFIED,
    LIFECYCLE_READY_TO_INVITE,
})

NORMALIZED_NAME_LENGTH = 255
NORMALIZED_PHONE_LENGTH = 32
NORMALIZED_DOMAIN_LENGTH = 255
NORMALIZED_INSTAGRAM_LENGTH = 150
NORMALIZED_ADDRESS_LENGTH = 500

_NAME_QUOTE_CHARS = str.maketrans({
    '«': None,
    '»': None,
    '"': None,
    "'": None,
    '“': None,
    '”': None,
    '„': None,
    '‹': None,
    '›': None,
})
_ADDRESS_PUNCT_RE = re.compile(r'[,.;:]+')
_WHITESPACE_RE = re.compile(r'\s+')
_INSTAGRAM_HOSTS = {'instagram.com', 'm.instagram.com'}
_INSTAGRAM_RESERVED = {
    'p',
    'reel',
    'reels',
    'stories',
    'explore',
    'accounts',
    'direct',
}


def normalize_seller_phone(value: str | None) -> str:
    """Canonical phone via core.phone_utils. Empty string when the value is unsafe."""
    return normalize_phone_for_whatsapp(value) or ''


def normalize_domain(value: str | None) -> str:
    raw = str(value or '').strip()
    if not raw:
        return ''
    candidate = raw if '://' in raw else 'http://' + raw.lstrip('/')
    host = urlsplit(candidate).hostname or ''
    host = host.lower().strip().rstrip('.')
    if host.startswith('www.'):
        host = host[4:]
    return host[:NORMALIZED_DOMAIN_LENGTH]


def normalize_instagram_identity(value: str | None) -> str:
    raw = str(value or '').strip()
    if not raw:
        return ''
    lowered = raw.lower()
    if 'instagram.com' in lowered or lowered.startswith(('http://', 'https://')):
        candidate = raw if '://' in raw else f'https://{raw.lstrip("/")}'
        parts = urlsplit(candidate)
        host = (parts.hostname or '').lower()
        if host.startswith('www.'):
            host = host[4:]
        if host not in _INSTAGRAM_HOSTS:
            return ''
        username = (parts.path or '').strip('/').split('/')[0]
    else:
        username = raw[1:] if raw.startswith('@') else raw
        username = username.split('/')[0].split('?')[0].split('#')[0]
    username = username.strip().strip('@').lower()
    if not username or username in _INSTAGRAM_RESERVED or any(char.isspace() for char in username):
        return ''
    return username[:NORMALIZED_INSTAGRAM_LENGTH]


def normalize_seller_name(value: str | None) -> str:
    text = unicodedata.normalize('NFKC', str(value or ''))
    text = text.translate(_NAME_QUOTE_CHARS)
    text = text.lower().strip()
    text = _WHITESPACE_RE.sub(' ', text)
    return text[:NORMALIZED_NAME_LENGTH]


def normalize_address(value: str | None) -> str:
    text = unicodedata.normalize('NFKC', str(value or ''))
    text = text.lower().strip()
    text = _ADDRESS_PUNCT_RE.sub(' ', text)
    text = _WHITESPACE_RE.sub(' ', text).strip()
    return text[:NORMALIZED_ADDRESS_LENGTH]


def legacy_record_is_enriched(
    *,
    whatsapp: str = '',
    website_url: str = '',
    checked_at=None,
) -> bool:
    return bool(str(whatsapp or '').strip() or str(website_url or '').strip() or checked_at)


def map_legacy_status_to_lifecycle(status: str, *, enriched: bool) -> str:
    """Conservative legacy → lifecycle map. Unknown statuses stay FOUND."""
    legacy = str(status or '').strip()
    if legacy == 'verified':
        return LIFECYCLE_READY_TO_INVITE
    if legacy == 'contacted':
        return LIFECYCLE_INVITED
    if legacy == 'registered':
        return LIFECYCLE_ACTIVE
    if legacy == 'duplicate':
        return LIFECYCLE_DUPLICATE
    if legacy in {'rejected', 'not_seller'}:
        return LIFECYCLE_REJECTED
    if legacy in {'new', 'needs_review'}:
        return LIFECYCLE_ENRICHED if enriched else LIFECYCLE_FOUND
    return LIFECYCLE_FOUND


def refresh_seller_lead_identity(lead, *, save: bool = True):
    """Fill normalized identity from the lead's current canonical fields.

    normalized_address is owned by location selection and is left unchanged.
    """
    lead.normalized_name = normalize_seller_name(lead.name)
    lead.normalized_phone = normalize_seller_phone(lead.whatsapp)[:NORMALIZED_PHONE_LENGTH]
    lead.normalized_domain = normalize_domain(lead.website_url)
    instagram_source = (lead.instagram_username or '').strip() or (lead.instagram_url or '')
    lead.normalized_instagram = normalize_instagram_identity(instagram_source)
    update_fields = [
        'normalized_name',
        'normalized_phone',
        'normalized_domain',
        'normalized_instagram',
        'updated_at',
    ]
    if save and getattr(lead, 'pk', None):
        lead.save(update_fields=update_fields)
    return lead
