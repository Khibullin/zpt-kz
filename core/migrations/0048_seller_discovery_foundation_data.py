"""Backfill Seller Discovery identity for existing SellerLead rows.

Conservative and idempotent: unknown legacy statuses stay ``found``, Brave is
recorded only when the stored URL host is Brave, and no external_id is invented.

Normalization and lifecycle mapping below are frozen copies. This migration
must not import core.services, so a later change to those helpers cannot
rewrite historical data if the migration is applied again on another database.
"""

import re
import unicodedata
from urllib.parse import urlsplit

from django.db import migrations

INSTAGRAM_LEGACY_SOURCE_TYPES = {
    'instagram_search',
    'instagram_hashtag',
    'instagram_profile',
    'instagram_post',
}
BACKFILL_KEY = 'seller_discovery_foundation'
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
_WHITESPACE_RE = re.compile(r'\s+')
_INSTAGRAM_HOSTS = {'instagram.com', 'm.instagram.com'}
_INSTAGRAM_RESERVED = {'p', 'reel', 'reels', 'stories', 'explore', 'accounts', 'direct'}


def _digits_only(raw_phone) -> str:
    if raw_phone is None:
        return ''
    return ''.join(char for char in str(raw_phone) if char.isdigit())


def _normalize_phone(raw_phone) -> str:
    """Frozen snapshot of core.phone_utils.normalize_phone_for_whatsapp."""
    digits = _digits_only(raw_phone)
    if not digits:
        return ''
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    elif len(digits) == 10:
        digits = '7' + digits
    if len(digits) < 11 or len(digits) > 15:
        return ''
    if digits.startswith(('0', '8')):
        return ''
    if len(digits) == 11 and not digits.startswith(('1', '7')):
        return ''
    if len(set(digits)) == 1:
        return ''
    return digits[:32]


def _normalize_domain(value) -> str:
    raw = str(value or '').strip()
    if not raw:
        return ''
    candidate = raw if '://' in raw else 'http://' + raw.lstrip('/')
    host = urlsplit(candidate).hostname or ''
    host = host.lower().strip().rstrip('.')
    if host.startswith('www.'):
        host = host[4:]
    return host[:255]


def _normalize_instagram(value) -> str:
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
    return username[:150]


def _normalize_name(value) -> str:
    text = unicodedata.normalize('NFKC', str(value or ''))
    text = text.translate(_NAME_QUOTE_CHARS)
    text = text.lower().strip()
    return _WHITESPACE_RE.sub(' ', text)[:255]


def _legacy_is_enriched(*, whatsapp='', website_url='', checked_at=None) -> bool:
    return bool(str(whatsapp or '').strip() or str(website_url or '').strip() or checked_at)


def _map_legacy_lifecycle(status: str, *, enriched: bool) -> str:
    legacy = str(status or '').strip()
    if legacy == 'verified':
        return 'ready_to_invite'
    if legacy == 'contacted':
        return 'invited'
    if legacy == 'registered':
        return 'active'
    if legacy == 'duplicate':
        return 'duplicate'
    if legacy in {'rejected', 'not_seller'}:
        return 'rejected'
    if legacy in {'new', 'needs_review'}:
        return 'enriched' if enriched else 'found'
    return 'found'


def _extraction_method(legacy_source_type: str) -> str:
    if legacy_source_type == 'manual':
        return 'manual'
    if legacy_source_type == 'web_search' or legacy_source_type in INSTAGRAM_LEGACY_SOURCE_TYPES:
        return 'search_result'
    return 'other'


def _source_plan(lead, normalize_domain):
    legacy = str(lead.source_type or '')
    source_url = str(lead.source_url or '')
    plans = []
    if legacy == 'web_search' and source_url:
        host = normalize_domain(source_url)
        if host in {'brave.com', 'search.brave.com'}:
            plans.append(('brave_search', 'brave', source_url))
        else:
            plans.append(('web_search', '', source_url))
    elif legacy in INSTAGRAM_LEGACY_SOURCE_TYPES:
        instagram_url = str(lead.instagram_url or '') or source_url
        if instagram_url:
            plans.append(('instagram', '', instagram_url))
    elif legacy == 'manual':
        plans.append(('manual', '', source_url))
    elif legacy == 'other' and source_url:
        plans.append(('other', '', source_url))

    instagram_url = str(lead.instagram_url or '')
    known_urls = {url for _, _, url in plans if url}
    if instagram_url and instagram_url not in known_urls and legacy == 'web_search':
        plans.append(('instagram', '', instagram_url))
    return plans


def _ensure_source(Source, lead, *, source_type, provider, source_url, seen_at):
    source_url = (source_url or '')[:500]
    provider = provider or ''
    if source_url:
        existing = Source.objects.filter(seller_lead_id=lead.id, source_url=source_url).first()
    else:
        existing = Source.objects.filter(
            seller_lead_id=lead.id,
            source_type=source_type,
            provider=provider,
            external_id='',
            source_url='',
        ).first()
    if existing is not None:
        return existing
    return Source.objects.create(
        seller_lead_id=lead.id,
        source_type=source_type,
        provider=provider,
        external_id='',
        source_url=source_url,
        display_name=(lead.name or '')[:255],
        first_seen_at=seen_at,
        last_seen_at=seen_at,
        fetched_at=None,
        source_confidence=None,
        is_active=True,
        metadata={'backfill': BACKFILL_KEY},
        raw_payload_hash='',
    )


def _ensure_evidence(Evidence, lead, source, *, field_name, value, normalized, method, observed_at):
    if not normalized:
        return None
    existing = Evidence.objects.filter(
        seller_lead_id=lead.id,
        field_name=field_name,
        normalized_value=normalized,
    ).first()
    if existing is not None:
        return existing
    return Evidence.objects.create(
        seller_lead_id=lead.id,
        source_id=getattr(source, 'id', None),
        field_name=field_name,
        value=value or normalized,
        normalized_value=normalized,
        confidence=None,
        extraction_method=method,
        observed_at=observed_at,
        is_selected=True,
        is_owner_verified=False,
    )


def forwards(apps, schema_editor):
    SellerLead = apps.get_model('core', 'SellerLead')
    Source = apps.get_model('core', 'SellerLeadSource')
    Evidence = apps.get_model('core', 'SellerLeadEvidence')

    for lead in SellerLead.objects.all().iterator():
        legacy_status = lead.status
        enriched = _legacy_is_enriched(
            whatsapp=lead.whatsapp,
            website_url=lead.website_url,
            checked_at=lead.checked_at,
        )
        seen_at = lead.checked_at or lead.collected_at
        lead.lifecycle_status = _map_legacy_lifecycle(legacy_status, enriched=enriched)
        lead.normalized_name = _normalize_name(lead.name)
        lead.normalized_phone = _normalize_phone(lead.whatsapp)
        lead.normalized_domain = _normalize_domain(lead.website_url)
        lead.normalized_instagram = _normalize_instagram(
            lead.instagram_username or lead.instagram_url,
        )
        if lead.last_seen_at is None:
            lead.last_seen_at = seen_at
        if lead.last_enriched_at is None and lead.whatsapp_found_at:
            lead.last_enriched_at = lead.whatsapp_found_at
        lead.status = legacy_status
        lead.save(update_fields=[
            'lifecycle_status',
            'normalized_name',
            'normalized_phone',
            'normalized_domain',
            'normalized_instagram',
            'last_seen_at',
            'last_enriched_at',
        ])

        primary_source = None
        for source_type, provider, source_url in _source_plan(lead, _normalize_domain):
            source = _ensure_source(
                Source,
                lead,
                source_type=source_type,
                provider=provider,
                source_url=source_url,
                seen_at=seen_at,
            )
            if primary_source is None:
                primary_source = source

        if primary_source is None:
            continue
        method = _extraction_method(lead.source_type)
        _ensure_evidence(
            Evidence,
            lead,
            primary_source,
            field_name='phone',
            value=lead.whatsapp,
            normalized=lead.normalized_phone,
            method=method,
            observed_at=lead.whatsapp_found_at or seen_at,
        )
        _ensure_evidence(
            Evidence,
            lead,
            primary_source,
            field_name='website',
            value=lead.website_url,
            normalized=lead.normalized_domain,
            method=method,
            observed_at=seen_at,
        )
        _ensure_evidence(
            Evidence,
            lead,
            primary_source,
            field_name='instagram',
            value=lead.instagram_username or lead.instagram_url,
            normalized=lead.normalized_instagram,
            method=method,
            observed_at=seen_at,
        )


def backwards(apps, schema_editor):
    SellerLead = apps.get_model('core', 'SellerLead')
    Source = apps.get_model('core', 'SellerLeadSource')
    Evidence = apps.get_model('core', 'SellerLeadEvidence')
    sources = Source.objects.filter(metadata__backfill=BACKFILL_KEY)
    Evidence.objects.filter(source__in=sources).delete()
    sources.delete()
    SellerLead.objects.update(
        lifecycle_status='found',
        normalized_name='',
        normalized_phone='',
        normalized_domain='',
        normalized_instagram='',
        normalized_address='',
        last_seen_at=None,
        last_enriched_at=None,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0047_seller_discovery_foundation'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
