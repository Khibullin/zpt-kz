from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib import parse

from django.db.models import Q
from django.utils import timezone

from core.models import (
    Seller,
    SellerLead,
    SellerLeadContactCandidate,
    normalize_seller_lead_whatsapp,
)
from core.services.seller_lead_search import (
    BraveSearchClient,
    SellerLeadSearchConfigError,
    SellerLeadSearchError,
    get_seller_search_settings,
)

logger = logging.getLogger(__name__)

CONFIDENCE_HIGH = 'high'
CONFIDENCE_MEDIUM = 'medium'
CONFIDENCE_LOW = 'low'
AUTO_SAVE_CONFIDENCE = frozenset({CONFIDENCE_HIGH, CONFIDENCE_MEDIUM})
SOURCE_TEXT_LIMIT = 400
DEFAULT_MAX_QUERIES_PER_LEAD = 3
DEFAULT_SEARCH_RESULT_COUNT = 5

WHATSAPP_WORD_RE = re.compile(r'whatsapp(?:\s+business)?', re.IGNORECASE)
WA_ME_RE = re.compile(r'wa\.me/(\+?\d+)', re.IGNORECASE)
API_WHATSAPP_RE = re.compile(
    r'api\.whatsapp\.com/send\?(?:[^"\'\s>]*&)?phone=(\d+)',
    re.IGNORECASE,
)
PHONE_IN_TEXT_RE = re.compile(
    r'(?:'
    r'\+7[\s\-(]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}'
    r'|8[\s\-(]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}'
    r'|7[\s\-(]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}'
    r'|\b7\d{10}\b'
    r'|\b8\d{10}\b'
    r')',
    re.IGNORECASE,
)
INSTAGRAM_USERNAME_RE = re.compile(r'@([a-z0-9._]{1,30})', re.IGNORECASE)
INSTAGRAM_PATH_USERNAME_RE = re.compile(
    r'instagram\.com/([a-z0-9._]{1,30})(?:/|$|\?)',
    re.IGNORECASE,
)
GENERIC_CATALOG_MARKERS = (
    'каталог',
    'справочник',
    'directory',
    'yellowpages',
    '2gis',
    'olx.kz',
)


class SellerLeadContactSearchError(Exception):
    """Базовая ошибка поиска контактов SellerLead."""


@dataclass(frozen=True)
class SearchResultPayload:
    title: str
    url: str
    description: str


@dataclass(frozen=True)
class WhatsAppCandidate:
    phone: str
    confidence: str
    source_url: str
    source_text: str
    evidence: str = ''


@dataclass(frozen=True)
class LeadContactEnrichmentOutcome:
    lead_id: int
    username: str
    phone: str
    confidence: str
    source_url: str
    source_text: str
    accepted: bool
    rejection_reason: str = ''


@dataclass(frozen=True)
class ConflictContactOutcome:
    username: str
    phone: str
    role: str
    label: str
    confidence: str
    source_url: str
    source_text: str
    reason: str


@dataclass
class ContactEnrichmentStats:
    leads_processed: int = 0
    queries_executed: int = 0
    candidates_found: int = 0
    high_confidence: int = 0
    medium_confidence: int = 0
    low_confidence: int = 0
    conflicts: int = 0
    ready_to_save: int = 0
    saved: int = 0
    errors: int = 0
    contact_candidates_created: int = 0
    contact_candidates_updated: int = 0
    conflict_candidates: int = 0
    pending_review_candidates: int = 0
    global_phone_conflicts: int = 0
    lead_outcomes: list[LeadContactEnrichmentOutcome] = field(default_factory=list)
    conflict_outcomes: list[ConflictContactOutcome] = field(default_factory=list)


def normalize_kz_whatsapp_phone(raw_value: str) -> str | None:
    digits = normalize_seller_lead_whatsapp(raw_value)
    if len(digits) != 11 or not digits.startswith('7'):
        return None
    if len(set(digits)) == 1:
        return None
    return digits


def _safe_source_text(*parts: str) -> str:
    text = ' '.join(part.strip() for part in parts if part and part.strip())
    return text[:SOURCE_TEXT_LIMIT]


def _extract_phones_from_wa_urls(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for pattern in (WA_ME_RE, API_WHATSAPP_RE):
        for match in pattern.finditer(text):
            phone = normalize_kz_whatsapp_phone(match.group(1))
            if phone:
                found.append((phone, match.group(0)))
    return found


def _extract_phones_from_text(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for match in PHONE_IN_TEXT_RE.finditer(text):
        phone = normalize_kz_whatsapp_phone(match.group(0))
        if phone:
            found.append((phone, match.group(0)))
    return found


def extract_whatsapp_candidates_from_fields(
    *,
    title: str,
    description: str,
    url: str,
) -> list[tuple[str, str, str]]:
    """Возвращает список (phone, source_fragment, source_kind)."""
    combined = f'{title}\n{description}\n{url}'
    candidates: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    for phone, fragment in _extract_phones_from_wa_urls(url):
        if phone not in seen:
            seen.add(phone)
            candidates.append((phone, fragment, 'wa_url'))

    for phone, fragment in _extract_phones_from_wa_urls(combined):
        if phone not in seen:
            seen.add(phone)
            candidates.append((phone, fragment, 'wa_url'))

    for phone, fragment in _extract_phones_from_text(combined):
        if phone not in seen:
            seen.add(phone)
            candidates.append((phone, fragment, 'text'))

    return candidates


def _whatsapp_word_near_phone(text: str, phone_fragment: str) -> bool:
    if not WHATSAPP_WORD_RE.search(text):
        return False
    try:
        index = text.lower().find(phone_fragment.lower())
    except AttributeError:
        index = -1
    if index == -1:
        return bool(WHATSAPP_WORD_RE.search(text))
    window_start = max(0, index - 80)
    window_end = min(len(text), index + len(phone_fragment) + 80)
    return bool(WHATSAPP_WORD_RE.search(text[window_start:window_end]))


def _extract_instagram_usernames(*parts: str) -> set[str]:
    usernames: set[str] = set()
    for part in parts:
        for match in INSTAGRAM_USERNAME_RE.finditer(part):
            usernames.add(match.group(1).lower())
        for match in INSTAGRAM_PATH_USERNAME_RE.finditer(part):
            usernames.add(match.group(1).lower())
    return usernames


def _name_tokens(name: str) -> set[str]:
    tokens = re.findall(r'[a-z0-9а-яё]{3,}', name.lower(), flags=re.IGNORECASE)
    return {token for token in tokens if token not in {'instagram', 'whatsapp', 'алматы'}}


def _is_generic_catalog_result(result: SearchResultPayload) -> bool:
    haystack = f'{result.title} {result.description} {result.url}'.lower()
    return any(marker in haystack for marker in GENERIC_CATALOG_MARKERS)


def determine_whatsapp_confidence(
    *,
    phone: str,
    source_kind: str,
    result: SearchResultPayload,
    lead: SellerLead,
) -> str:
    text = f'{result.title} {result.description} {result.url}'
    username = (lead.instagram_username or '').lower()
    name_tokens = _name_tokens(lead.name)

    if source_kind == 'wa_url' or 'wa.me/' in result.url.lower() or 'api.whatsapp.com' in result.url.lower():
        return CONFIDENCE_HIGH

    if _whatsapp_word_near_phone(text, phone) or WHATSAPP_WORD_RE.search(text):
        if username and username in text.lower():
            return CONFIDENCE_HIGH
        if _whatsapp_word_near_phone(text, phone):
            return CONFIDENCE_HIGH

    if username and username in text.lower():
        return CONFIDENCE_MEDIUM

    if username and username in result.url.lower():
        return CONFIDENCE_MEDIUM

    if name_tokens and any(token in text.lower() for token in name_tokens):
        return CONFIDENCE_MEDIUM

    return CONFIDENCE_LOW


def build_contact_search_queries(
    *,
    username: str,
    name: str,
    city: str,
) -> list[str]:
    queries: list[str] = []
    if username:
        queries.append(f'site:instagram.com/{username} WhatsApp')
        queries.append(f'"{username}" WhatsApp')
        queries.append(f'"{username}" wa.me')
        queries.append(f'"{username}" "+7"')
    if name and city:
        queries.append(f'"{name}" {city} WhatsApp')
    return queries


def _phone_used_by_other_lead(phone: str, *, lead_id: int) -> bool:
    return SellerLead.objects.filter(whatsapp=phone).exclude(pk=lead_id).exists()


def _phone_used_by_registered_seller(phone: str) -> bool:
    for seller in Seller.objects.only('whatsapp', 'phone2'):
        for raw_phone in (seller.whatsapp, seller.phone2):
            if normalize_kz_whatsapp_phone(raw_phone) == phone:
                return True
    return False


def _result_conflicts_with_username(result: SearchResultPayload, *, username: str) -> bool:
    if not username:
        return False
    mentioned = _extract_instagram_usernames(result.title, result.description, result.url)
    if not mentioned:
        return False
    return username.lower() not in mentioned and len(mentioned) > 0


def extract_candidates_from_result(
    result: SearchResultPayload,
    lead: SellerLead,
) -> list[WhatsAppCandidate]:
    username = (lead.instagram_username or '').strip()
    if _result_conflicts_with_username(result, username=username):
        return []

    if _is_generic_catalog_result(result) and username.lower() not in result.url.lower():
        return []

    candidates: list[WhatsAppCandidate] = []
    for phone, fragment, source_kind in extract_whatsapp_candidates_from_fields(
        title=result.title,
        description=result.description,
        url=result.url,
    ):
        confidence = determine_whatsapp_confidence(
            phone=phone,
            source_kind=source_kind,
            result=result,
            lead=lead,
        )
        source_text = _safe_source_text(result.title, fragment, result.description)
        candidates.append(
            WhatsAppCandidate(
                phone=phone,
                confidence=confidence,
                source_url=result.url,
                source_text=source_text,
                evidence=fragment,
            ),
        )
    return candidates


def _infer_contact_source_type(source_url: str) -> str:
    lower = (source_url or '').lower()
    if 'instagram.com/' in lower:
        return 'instagram_snippet'
    if 'wa.me/' in lower or 'api.whatsapp.com' in lower:
        return 'wa_me'
    if 'facebook.com/' in lower:
        return 'facebook'
    if any(marker in lower for marker in ('2gis', 'orgs.biz', 'yellowpages', 'olx.kz')):
        return 'directory'
    if lower.startswith('http'):
        return 'website'
    return 'other'


def upsert_contact_candidate_from_whatsapp(
    lead: SellerLead,
    candidate: WhatsAppCandidate,
    *,
    status: str,
) -> tuple[bool, bool]:
    """Возвращает (created, updated)."""
    value = normalize_kz_whatsapp_phone(candidate.phone)
    if not value:
        return False, False

    existing = SellerLeadContactCandidate.objects.filter(
        seller_lead=lead,
        contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
        value=value,
    ).first()
    source_type = _infer_contact_source_type(candidate.source_url)
    if existing:
        existing.confidence = candidate.confidence
        existing.source_url = candidate.source_url[:500]
        existing.source_text = candidate.source_text[:SOURCE_TEXT_LIMIT]
        existing.source_type = source_type
        existing.status = status
        existing.is_primary = False
        existing.save(
            update_fields=[
                'confidence',
                'source_url',
                'source_text',
                'source_type',
                'status',
                'is_primary',
                'updated_at',
            ],
        )
        return False, True

    SellerLeadContactCandidate.objects.create(
        seller_lead=lead,
        contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
        value=value,
        confidence=candidate.confidence,
        source_url=candidate.source_url[:500],
        source_text=candidate.source_text[:SOURCE_TEXT_LIMIT],
        source_type=source_type,
        status=status,
        is_primary=False,
        found_at=timezone.now(),
    )
    return True, False


def _select_best_candidate(
    candidates: Iterable[WhatsAppCandidate],
    *,
    lead: SellerLead,
) -> tuple[WhatsAppCandidate | None, str, list[WhatsAppCandidate]]:
    filtered: list[WhatsAppCandidate] = []
    for candidate in candidates:
        if _phone_used_by_other_lead(candidate.phone, lead_id=lead.pk):
            continue
        if _phone_used_by_registered_seller(candidate.phone):
            continue
        filtered.append(candidate)

    if not filtered:
        return None, 'подходящий номер не найден', []

    for confidence in (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW):
        level_candidates = [item for item in filtered if item.confidence == confidence]
        if not level_candidates:
            continue
        unique_by_phone: dict[str, WhatsAppCandidate] = {}
        for item in level_candidates:
            unique_by_phone.setdefault(item.phone, item)
        if len(unique_by_phone) > 1:
            return None, f'найдено несколько разных номеров с уверенностью {confidence}', list(unique_by_phone.values())
        best = level_candidates[0]
        if confidence not in AUTO_SAVE_CONFIDENCE:
            return None, f'уверенность {confidence} слишком низкая для автосохранения', []
        return best, '', []

    return None, 'подходящий номер не найден', []


def enrich_seller_lead_contacts(
    *,
    username: str | None = None,
    limit: int | None = None,
    max_queries_per_lead: int = DEFAULT_MAX_QUERIES_PER_LEAD,
    dry_run: bool = False,
    client: BraveSearchClient | None = None,
    search_settings: dict[str, Any] | None = None,
) -> ContactEnrichmentStats:
    settings_data = search_settings or get_seller_search_settings()
    stats = ContactEnrichmentStats()

    if settings_data.get('provider') != 'brave':
        raise SellerLeadSearchConfigError(
            f"Неподдерживаемый SELLER_SEARCH_PROVIDER: {settings_data.get('provider')}",
        )
    if not settings_data.get('enabled'):
        raise SellerLeadSearchConfigError(
            'SELLER_SEARCH_ENABLED=False. Реальные запросы к поисковому API отключены.',
        )
    api_key = settings_data.get('api_key', '')
    if not api_key:
        raise SellerLeadSearchConfigError(
            'BRAVE_SEARCH_API_KEY не задан. Укажите ключ в переменных окружения.',
        )

    search_client = client or BraveSearchClient(api_key=api_key)
    leads_qs = SellerLead.objects.filter(
        status=SellerLead.STATUS_NEEDS_REVIEW,
        whatsapp='',
    ).exclude(instagram_username='').order_by('id')
    if username:
        leads_qs = leads_qs.filter(instagram_username=username)
    if limit is not None:
        leads_qs = leads_qs[: max(1, limit)]

    for lead in leads_qs:
        stats.leads_processed += 1
        lead_candidates: list[WhatsAppCandidate] = []
        queries = build_contact_search_queries(
            username=lead.instagram_username,
            name=lead.name,
            city=lead.city,
        )
        queries_executed_for_lead = 0
        try:
            for query in queries:
                if queries_executed_for_lead >= max(1, max_queries_per_lead):
                    break
                if any(item.confidence == CONFIDENCE_HIGH for item in lead_candidates):
                    break
                results = search_client.search(query, count=DEFAULT_SEARCH_RESULT_COUNT)
                stats.queries_executed += 1
                queries_executed_for_lead += 1
                for row in results:
                    payload = SearchResultPayload(
                        title=row.get('title', ''),
                        url=row.get('url', ''),
                        description=row.get('description', ''),
                    )
                    lead_candidates.extend(extract_candidates_from_result(payload, lead))
                if any(item.confidence == CONFIDENCE_HIGH for item in lead_candidates):
                    break
        except SellerLeadSearchError:
            stats.errors += 1
            logger.exception(
                'Seller lead contact search failed for username=%r',
                lead.instagram_username,
            )
            stats.lead_outcomes.append(
                LeadContactEnrichmentOutcome(
                    lead_id=lead.pk,
                    username=lead.instagram_username,
                    phone='',
                    confidence='',
                    source_url='',
                    source_text='',
                    accepted=False,
                    rejection_reason='ошибка поискового API',
                ),
            )
            continue

        stats.candidates_found += len(lead_candidates)
        for candidate in lead_candidates:
            if candidate.confidence == CONFIDENCE_HIGH:
                stats.high_confidence += 1
            elif candidate.confidence == CONFIDENCE_MEDIUM:
                stats.medium_confidence += 1
            else:
                stats.low_confidence += 1

        selected, rejection_reason, conflict_candidates = _select_best_candidate(lead_candidates, lead=lead)
        if rejection_reason.startswith('найдено несколько'):
            stats.conflicts += 1
            if not dry_run:
                for conflict_candidate in conflict_candidates:
                    created, updated = upsert_contact_candidate_from_whatsapp(
                        lead,
                        conflict_candidate,
                        status=SellerLeadContactCandidate.STATUS_CONFLICT,
                    )
                    if created:
                        stats.contact_candidates_created += 1
                    elif updated:
                        stats.contact_candidates_updated += 1
                    stats.conflict_candidates += 1
                    stats.conflict_outcomes.append(
                        ConflictContactOutcome(
                            username=lead.instagram_username,
                            phone=conflict_candidate.phone,
                            role=SellerLeadContactCandidate.ROLE_UNKNOWN,
                            label='',
                            confidence=conflict_candidate.confidence,
                            source_url=conflict_candidate.source_url,
                            source_text=conflict_candidate.source_text[:200],
                            reason=rejection_reason,
                        ),
                    )
            else:
                for conflict_candidate in conflict_candidates:
                    stats.conflict_outcomes.append(
                        ConflictContactOutcome(
                            username=lead.instagram_username,
                            phone=conflict_candidate.phone,
                            role=SellerLeadContactCandidate.ROLE_UNKNOWN,
                            label='',
                            confidence=conflict_candidate.confidence,
                            source_url=conflict_candidate.source_url,
                            source_text=conflict_candidate.source_text[:200],
                            reason=rejection_reason,
                        ),
                    )

        global_conflict_phones = {
            candidate.phone
            for candidate in lead_candidates
            if _phone_used_by_other_lead(candidate.phone, lead_id=lead.pk)
        }
        stats.global_phone_conflicts += len(global_conflict_phones)

        accepted = selected is not None
        if accepted:
            stats.ready_to_save += 1
            if not dry_run:
                lead.whatsapp = selected.phone
                lead.whatsapp_source_url = selected.source_url[:500]
                lead.whatsapp_source_text = selected.source_text
                lead.whatsapp_confidence = selected.confidence
                lead.whatsapp_found_at = timezone.now()
                lead.save(
                    update_fields=[
                        'whatsapp',
                        'whatsapp_source_url',
                        'whatsapp_source_text',
                        'whatsapp_confidence',
                        'whatsapp_found_at',
                        'updated_at',
                    ],
                )
                stats.saved += 1

        stats.lead_outcomes.append(
            LeadContactEnrichmentOutcome(
                lead_id=lead.pk,
                username=lead.instagram_username,
                phone=selected.phone if selected else '',
                confidence=selected.confidence if selected else '',
                source_url=selected.source_url if selected else '',
                source_text=selected.source_text if selected else '',
                accepted=accepted,
                rejection_reason=rejection_reason,
            ),
        )

    return stats
