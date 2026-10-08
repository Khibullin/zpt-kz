"""One-off public audit for already registered ZPT sellers.

The campaign only enriches existing SellerProfile rows. It never creates Seller,
User, SellerProfile, Product or messages and never changes request routing,
seller availability, WhatsApp, selected brands/categories/models or passwords.

Progress is stored in the temporary core_registered_seller_public_audit table
created by migration 0061. Every registered seller is claimed once, so the
minute cron is bounded and becomes a no-op after the snapshot is complete.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib import parse

from django.conf import settings
from django.db import connection, transaction

from catalog.models import SellerProfile
from core.models import Seller
from core.phone_utils import normalize_kz_phone
from core.services.seller_contact_website import (
    crawl_official_website,
    distinctive_name_tokens,
)
from core.services.seller_discovery_identity import normalize_seller_name
from core.services.seller_lead_search import (
    BraveSearchClient,
    SellerLeadSearchError,
    build_instagram_profile_url,
    parse_instagram_profile_url,
)

CAMPAIGN_TAG = 'registered_seller_public_audit_2026_10_08'
STATE_TABLE = 'core_registered_seller_public_audit'

SKIPPED_HOSTS = frozenset({
    '2gis.kz', '2gis.com', 'google.com', 'google.kz', 'yandex.kz', 'yandex.ru',
    'yandex.com', 'maps.yandex.ru', 'instagram.com', 'facebook.com',
    'youtube.com', 'tiktok.com', 'kolesa.kz', 'kaspi.kz', 'olx.kz', 'satu.kz',
    'spravka.kz', 'zoon.kz', 'orgpage.kz', 'biznesinfo.kz',
})

# Exact-phone / exact-identity facts verified during this audit. They are
# guarded by both seller name and phone, and only fill blank profile fields.
CURATED_PROFILE_FACTS = {
    476: {
        'name': 'ABUCAR',
        'phone': '77782477987',
        'instagram': 'https://www.instagram.com/abucar.kz/',
    },
    488: {
        'name': 'Тулпар',
        'phone': '77022073737',
        'instagram': 'https://www.instagram.com/tulpar_avtocenter/',
    },
    527: {
        'name': 'Tiptronic',
        'phone': '77023226888',
        'website': 'http://www.t-tronic.kz/',
        'instagram': 'https://www.instagram.com/tiptronic_autoparts/',
    },
}


@dataclass
class RegisteredSellerAuditBatchResult:
    claimed: int = 0
    completed: int = 0
    errors: int = 0
    websites_added: int = 0
    instagrams_added: int = 0
    curated_changes: int = 0
    remaining: int = 0
    details: list[dict] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.remaining == 0


def _state_table_exists() -> bool:
    with connection.cursor() as cursor:
        return STATE_TABLE in connection.introspection.table_names(cursor)


def _claim_seller_ids(batch_size: int) -> list[int]:
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT seller_id
                FROM {STATE_TABLE}
                WHERE status = 'pending'
                ORDER BY seller_id
                FOR UPDATE SKIP LOCKED
                LIMIT %s
                """,
                [batch_size],
            )
            ids = [row[0] for row in cursor.fetchall()]
            if ids:
                cursor.execute(
                    f"""
                    UPDATE {STATE_TABLE}
                    SET status = 'running',
                        attempts = attempts + 1,
                        started_at = COALESCE(started_at, NOW()),
                        updated_at = NOW()
                    WHERE seller_id = ANY(%s)
                    """,
                    [ids],
                )
            return ids


def _finish_state(seller_id: int, *, payload: dict, error: str = '') -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE {STATE_TABLE}
            SET status = 'done',
                result = %s::jsonb,
                error = %s,
                finished_at = NOW(),
                updated_at = NOW()
            WHERE seller_id = %s
            """,
            [json.dumps(payload, ensure_ascii=False), error[:2000], seller_id],
        )


def _remaining_count() -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT COUNT(*) FROM {STATE_TABLE} WHERE status <> 'done'"
        )
        return int(cursor.fetchone()[0])


def _apply_curated_profile_facts() -> int:
    changed = 0
    for seller_id, fact in CURATED_PROFILE_FACTS.items():
        seller = Seller.objects.filter(pk=seller_id, user_id__isnull=False).first()
        if seller is None:
            continue
        if seller.name.strip().casefold() != fact['name'].strip().casefold():
            continue
        if normalize_kz_phone(seller.whatsapp) != fact['phone']:
            continue
        profile = SellerProfile.objects.filter(user_id=seller.user_id).first()
        if profile is None:
            continue
        fields = []
        website = fact.get('website', '')
        instagram = fact.get('instagram', '')
        if website and not (profile.website or '').strip():
            profile.website = website
            fields.append('website')
        if instagram and not (profile.instagram or '').strip():
            profile.instagram = instagram
            fields.append('instagram')
        if fields:
            profile.save(update_fields=fields)
            changed += 1
    return changed


def _phone_values_in_text(text: str) -> set[str]:
    values: set[str] = set()
    for raw in re.findall(r'\+?\d[\d\s()\-]{8,20}\d', text or ''):
        normalized = normalize_kz_phone(raw)
        if normalized:
            values.add(normalized)
    return values


def _name_matches_text(name: str, text: str) -> bool:
    normalized_text = normalize_seller_name(text or '')
    tokens = distinctive_name_tokens(name)
    if not tokens:
        return False
    if len(tokens) == 1:
        return tokens[0] in normalized_text
    matched = sum(1 for token in tokens if token in normalized_text)
    return matched >= 2


def _strong_name_city(name: str, city: str, text: str) -> bool:
    if not city or city.casefold() not in (text or '').casefold():
        return False
    tokens = distinctive_name_tokens(name)
    if len(tokens) < 2:
        return False
    normalized_text = normalize_seller_name(text or '')
    return sum(1 for token in tokens if token in normalized_text) >= 2


def _hostname(url: str) -> str:
    host = (parse.urlsplit(url or '').hostname or '').casefold().rstrip('.')
    return host[4:] if host.startswith('www.') else host


def _is_candidate_website(url: str) -> bool:
    parts = parse.urlsplit(str(url or '').strip())
    if parts.scheme not in {'http', 'https'} or not parts.hostname:
        return False
    host = _hostname(url)
    return not any(host == item or host.endswith('.' + item) for item in SKIPPED_HOSTS)


def _result_text(hit: dict[str, str]) -> str:
    return ' '.join([
        str(hit.get('title') or ''),
        str(hit.get('description') or ''),
        str(hit.get('url') or ''),
    ])


def _search_hits(seller: Seller, client: BraveSearchClient) -> tuple[list[dict[str, str]], str]:
    phone = normalize_kz_phone(seller.whatsapp) or ''
    query = f'"{seller.name}" {seller.city} {phone} автозапчасти'
    try:
        hits = client.search(query, count=10)
        return hits, ''
    except SellerLeadSearchError as exc:
        return [], str(exc)


def _crawl_profile_website(
    seller: Seller,
    profile: SellerProfile,
    url: str,
    *,
    hit_text: str = '',
    prior: bool = False,
):
    phone = normalize_kz_phone(seller.whatsapp) or ''
    result = crawl_official_website(
        url,
        lead_name=seller.name,
        city=seller.city,
        address=profile.address,
        phone=phone,
        instagram=profile.instagram,
        known_domain=_hostname(profile.website) if prior else '',
        domain_is_prior=prior,
    )
    exact_phone = bool(phone) and any(
        item.field_name in {'phone', 'whatsapp'}
        and normalize_kz_phone(item.value) == phone
        for item in result.contacts
    )
    accepted = bool(result.identity_accepted)
    if prior and result.pages_fetched:
        accepted = True
    elif exact_phone and _name_matches_text(seller.name, hit_text):
        accepted = True
    return result, accepted


def _instagram_from_contacts(contacts) -> str:
    for item in contacts:
        if item.field_name != 'instagram':
            continue
        handle = str(item.value or '').strip().lstrip('@')
        if handle:
            return build_instagram_profile_url(handle)
    return ''


def _audit_one(seller_id: int) -> dict:
    seller = Seller.objects.filter(pk=seller_id, user_id__isnull=False).first()
    if seller is None:
        return {'outcome': 'seller_missing'}

    profile = SellerProfile.objects.filter(user_id=seller.user_id).first()
    if profile is None:
        return {'outcome': 'profile_missing', 'seller': seller.name}

    payload = {
        'outcome': 'checked',
        'seller': seller.name,
        'city': seller.city,
        'phone': normalize_kz_phone(seller.whatsapp) or '',
        'website_before': (profile.website or '').strip(),
        'instagram_before': (profile.instagram or '').strip(),
        'website_added': '',
        'instagram_added': '',
        'search_hits': 0,
        'websites_crawled': 0,
        'search_error': '',
    }

    # Re-read an already stored website first. It can expose the official
    # Instagram without any search-engine inference.
    if (profile.website or '').strip():
        crawl, accepted = _crawl_profile_website(
            seller,
            profile,
            profile.website.strip(),
            prior=True,
        )
        payload['websites_crawled'] += int(crawl.pages_fetched > 0)
        if accepted and not (profile.instagram or '').strip():
            instagram = _instagram_from_contacts(crawl.contacts)
            if instagram:
                profile.instagram = instagram
                profile.save(update_fields=['instagram'])
                payload['instagram_added'] = instagram

    api_key = (getattr(settings, 'BRAVE_SEARCH_API_KEY', '') or '').strip()
    if not api_key:
        payload['search_error'] = 'BRAVE_SEARCH_API_KEY missing'
        payload['outcome'] = 'checked_without_search'
        return payload

    try:
        client = BraveSearchClient(api_key=api_key)
    except SellerLeadSearchError as exc:
        payload['search_error'] = str(exc)
        payload['outcome'] = 'checked_without_search'
        return payload

    hits, search_error = _search_hits(seller, client)
    payload['search_hits'] = len(hits)
    payload['search_error'] = search_error

    phone = normalize_kz_phone(seller.whatsapp) or ''
    instagram_candidates: list[tuple[str, str]] = []
    website_candidates: list[tuple[str, str]] = []
    seen_hosts: set[str] = set()

    for hit in hits:
        url = str(hit.get('url') or '').strip()
        text = _result_text(hit)
        parsed_instagram = parse_instagram_profile_url(url)
        if parsed_instagram:
            instagram_candidates.append((parsed_instagram['profile_url'], text))
            continue
        if not _is_candidate_website(url):
            continue
        host = _hostname(url)
        if not host or host in seen_hosts:
            continue
        seen_hosts.add(host)
        website_candidates.append((url, text))
        if len(website_candidates) >= 4:
            break

    # Direct Instagram search results are accepted only with exact phone
    # evidence or a strong multi-token business-name + city match.
    if not (profile.instagram or '').strip():
        for instagram_url, text in instagram_candidates:
            if (
                (phone and phone in _phone_values_in_text(text))
                or _strong_name_city(seller.name, seller.city, text)
            ):
                profile.instagram = instagram_url
                profile.save(update_fields=['instagram'])
                payload['instagram_added'] = instagram_url
                break

    # A discovered website is saved only after its own HTML confirms identity.
    if not (profile.website or '').strip():
        for candidate_url, text in website_candidates:
            crawl, accepted = _crawl_profile_website(
                seller,
                profile,
                candidate_url,
                hit_text=text,
                prior=False,
            )
            payload['websites_crawled'] += int(crawl.pages_fetched > 0)
            if not accepted:
                continue
            website = crawl.final_url or candidate_url
            profile.website = website
            fields = ['website']
            payload['website_added'] = website
            if not (profile.instagram or '').strip():
                instagram = _instagram_from_contacts(crawl.contacts)
                if instagram:
                    profile.instagram = instagram
                    fields.append('instagram')
                    payload['instagram_added'] = instagram
            profile.save(update_fields=fields)
            break

    return payload


def process_registered_seller_public_audit_batch(
    *,
    batch_size: int = 4,
) -> RegisteredSellerAuditBatchResult:
    result = RegisteredSellerAuditBatchResult()
    if not _state_table_exists():
        result.errors = 1
        result.details.append({'error': f'{STATE_TABLE} does not exist'})
        return result

    result.curated_changes = _apply_curated_profile_facts()
    seller_ids = _claim_seller_ids(max(1, min(int(batch_size), 8)))
    result.claimed = len(seller_ids)

    for seller_id in seller_ids:
        try:
            payload = _audit_one(seller_id)
            _finish_state(seller_id, payload=payload)
            result.completed += 1
            if payload.get('website_added'):
                result.websites_added += 1
            if payload.get('instagram_added'):
                result.instagrams_added += 1
            result.details.append({'seller_id': seller_id, **payload})
        except Exception as exc:
            payload = {
                'outcome': 'error',
                'error_type': type(exc).__name__,
            }
            _finish_state(seller_id, payload=payload, error=str(exc))
            result.completed += 1
            result.errors += 1
            result.details.append({
                'seller_id': seller_id,
                'outcome': 'error',
                'error_type': type(exc).__name__,
            })

    result.remaining = _remaining_count()
    return result
