from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from django.db import transaction
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
CONFIDENCE_RANK = {
    CONFIDENCE_HIGH: 3,
    CONFIDENCE_MEDIUM: 2,
    CONFIDENCE_LOW: 1,
}
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
    action: str = ''


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


def _confidence_rank(confidence: str) -> int:
    return CONFIDENCE_RANK.get(confidence, 0)


def _is_conflict_rejection(reason: str) -> bool:
    return reason.startswith((
        'найдено несколько',
        'несколько номеров',
        'high и medium',
    ))


def _filter_usable_candidates(
    candidates: Iterable[WhatsAppCandidate],
    *,
    lead: SellerLead,
) -> list[WhatsAppCandidate]:
    filtered: list[WhatsAppCandidate] = []
    for candidate in candidates:
        if _phone_used_by_other_lead(candidate.phone, lead_id=lead.pk):
            continue
        if _phone_used_by_registered_seller(candidate.phone):
            continue
        filtered.append(candidate)
    return filtered


def _merge_candidate_evidence(
    existing: WhatsAppCandidate,
    new: WhatsAppCandidate,
) -> WhatsAppCandidate:
    if _confidence_rank(new.confidence) > _confidence_rank(existing.confidence):
        return new
    if _confidence_rank(new.confidence) < _confidence_rank(existing.confidence):
        return existing
    if len(new.source_text) > len(existing.source_text):
        return new
    if len(new.evidence) > len(existing.evidence):
        return new
    return existing


def _dedupe_candidates_by_phone(
    candidates: Iterable[WhatsAppCandidate],
) -> list[WhatsAppCandidate]:
    unique_by_phone: dict[str, WhatsAppCandidate] = {}
    for candidate in candidates:
        if candidate.phone in unique_by_phone:
            unique_by_phone[candidate.phone] = _merge_candidate_evidence(
                unique_by_phone[candidate.phone],
                candidate,
            )
        else:
            unique_by_phone[candidate.phone] = candidate
    return list(unique_by_phone.values())


def _normalize_source_url(source_url: str) -> str:
    return (source_url or '').strip().lower().rstrip('/')


def _is_lead_profile_source(source_url: str, lead: SellerLead) -> bool:
    username = (lead.instagram_username or '').strip().lower()
    if not username:
        return False
    normalized = _normalize_source_url(source_url)
    return f'instagram.com/{username}' in normalized


def _candidate_appears_in_readable_text(phone: str, result: SearchResultPayload) -> bool:
    readable = f'{result.title}\n{result.description}'
    for match in PHONE_IN_TEXT_RE.finditer(readable):
        normalized = normalize_kz_whatsapp_phone(match.group(0))
        if normalized == phone:
            return True
    return False


def _is_2gis_catalog_entity_phone(
    phone: str,
    *,
    result: SearchResultPayload,
    source_kind: str,
) -> bool:
    source_url = _normalize_source_url(result.url)
    if '2gis' not in source_url:
        return False
    if _candidate_appears_in_readable_text(phone, result):
        return False
    if source_kind == 'wa_url':
        return False
    if '/firm/' not in source_url and '/geo/' not in source_url:
        return False
    url_digits = re.sub(r'\D', '', result.url)
    return phone in url_digits


def _infer_role_from_candidate(candidate: WhatsAppCandidate) -> str:
    text = f'{candidate.source_text} {candidate.evidence}'.lower()
    if any(marker in text for marker in ('сервис', 'service', 'сто', 'autoservice')):
        return SellerLeadContactCandidate.ROLE_SERVICE
    if any(marker in text for marker in ('магазин', 'shop', 'store', 'бутик')):
        return SellerLeadContactCandidate.ROLE_SHOP
    return SellerLeadContactCandidate.ROLE_UNKNOWN


def _detect_saveable_conflict(
    unique_candidates: list[WhatsAppCandidate],
    *,
    lead: SellerLead,
) -> tuple[str, list[WhatsAppCandidate]]:
    saveable = [
        candidate
        for candidate in unique_candidates
        if candidate.confidence in AUTO_SAVE_CONFIDENCE
    ]
    if len(saveable) < 2:
        return '', []

    by_source: dict[str, list[WhatsAppCandidate]] = {}
    for candidate in saveable:
        by_source.setdefault(_normalize_source_url(candidate.source_url), []).append(candidate)

    for source_url, source_candidates in by_source.items():
        source_phones = _dedupe_candidates_by_phone(source_candidates)
        if len(source_phones) > 1 and (
            _is_lead_profile_source(source_url, lead)
            or source_url.startswith('http')
        ):
            return (
                f'несколько номеров в одном источнике ({source_url or "без URL"})',
                saveable,
            )

    max_rank = max(_confidence_rank(candidate.confidence) for candidate in saveable)
    top_tier = [
        candidate
        for candidate in saveable
        if _confidence_rank(candidate.confidence) == max_rank
    ]
    top_unique = _dedupe_candidates_by_phone(top_tier)
    if len(top_unique) > 1:
        return (
            f'найдено несколько разных номеров с уверенностью {top_unique[0].confidence}',
            top_unique,
        )

    highs = [candidate for candidate in saveable if candidate.confidence == CONFIDENCE_HIGH]
    mediums = [candidate for candidate in saveable if candidate.confidence == CONFIDENCE_MEDIUM]
    if len(highs) == 1 and mediums:
        high_candidate = highs[0]
        for medium_candidate in mediums:
            if (
                _normalize_source_url(high_candidate.source_url)
                == _normalize_source_url(medium_candidate.source_url)
                or (
                    _is_lead_profile_source(high_candidate.source_url, lead)
                    and _is_lead_profile_source(medium_candidate.source_url, lead)
                )
            ):
                return (
                    'high и medium из одного профиля требуют ручной проверки',
                    _dedupe_candidates_by_phone(saveable),
                )

    return '', []


def _should_stop_queries_after_current(
    candidates: Iterable[WhatsAppCandidate],
    *,
    lead: SellerLead,
) -> bool:
    unique = _dedupe_candidates_by_phone(_filter_usable_candidates(candidates, lead=lead))
    saveable = [
        candidate
        for candidate in unique
        if candidate.confidence in AUTO_SAVE_CONFIDENCE
    ]
    if len(saveable) != 1:
        return False
    return saveable[0].confidence == CONFIDENCE_HIGH


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


def _phone_used_by_other_lead(phone: str, *, lead_id: int | None) -> bool:
    queryset = SellerLead.objects.filter(whatsapp=phone)
    if lead_id is not None:
        queryset = queryset.exclude(pk=lead_id)
    return queryset.exists()


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
        if _is_2gis_catalog_entity_phone(
            phone,
            result=result,
            source_kind=source_kind,
        ):
            continue
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
    role: str | None = None,
) -> tuple[bool, bool]:
    """Возвращает (created, updated)."""
    value = normalize_kz_whatsapp_phone(candidate.phone)
    if not value:
        return False, False

    resolved_role = role or _infer_role_from_candidate(candidate)
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
        existing.role = resolved_role
        existing.save(
            update_fields=[
                'confidence',
                'source_url',
                'source_text',
                'source_type',
                'status',
                'is_primary',
                'role',
                'updated_at',
            ],
        )
        return False, True

    SellerLeadContactCandidate.objects.create(
        seller_lead=lead,
        contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
        value=value,
        role=resolved_role,
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
    unique = _dedupe_candidates_by_phone(_filter_usable_candidates(candidates, lead=lead))
    if not unique:
        return None, 'подходящий номер не найден', []

    conflict_reason, conflict_candidates = _detect_saveable_conflict(unique, lead=lead)
    if conflict_reason:
        return None, conflict_reason, conflict_candidates

    saveable = [
        candidate
        for candidate in unique
        if candidate.confidence in AUTO_SAVE_CONFIDENCE
    ]
    if len(saveable) == 1:
        return saveable[0], '', []

    if not saveable:
        lowest = min(unique, key=lambda item: _confidence_rank(item.confidence))
        return None, f'уверенность {lowest.confidence} слишком низкая для автосохранения', []

    return None, 'подходящий номер не найден', []


def enrich_seller_lead_contacts(
    *,
    username: str | None = None,
    limit: int | None = None,
    lead_ids: Iterable[int] | None = None,
    leads: Iterable[SellerLead] | None = None,
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
    if leads is not None:
        lead_list = list(leads)
        if limit is not None:
            lead_list = lead_list[: max(1, limit)]
    else:
        leads_qs = SellerLead.objects.filter(
            status=SellerLead.STATUS_NEEDS_REVIEW,
            whatsapp='',
        ).exclude(instagram_username='').order_by('id')
        if username:
            leads_qs = leads_qs.filter(instagram_username=username)
        if lead_ids is not None:
            lead_ids_list = list(lead_ids)
            if not lead_ids_list:
                return stats
            leads_qs = leads_qs.filter(pk__in=lead_ids_list)
        if limit is not None:
            leads_qs = leads_qs[: max(1, limit)]
        lead_list = list(leads_qs)

    for lead in lead_list:
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
                if _should_stop_queries_after_current(lead_candidates, lead=lead):
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
        is_conflict = _is_conflict_rejection(rejection_reason)
        if is_conflict:
            stats.conflicts += 1
            conflict_candidates = _dedupe_candidates_by_phone(conflict_candidates)
            if not dry_run:
                try:
                    with transaction.atomic():
                        for conflict_candidate in conflict_candidates:
                            created, updated = upsert_contact_candidate_from_whatsapp(
                                lead,
                                conflict_candidate,
                                status=SellerLeadContactCandidate.STATUS_CONFLICT,
                            )
                            if created:
                                stats.contact_candidates_created += 1
                                action = 'создан'
                            elif updated:
                                stats.contact_candidates_updated += 1
                                action = 'обновлён'
                            else:
                                action = 'пропущен'
                            stats.conflict_candidates += 1
                            stats.conflict_outcomes.append(
                                ConflictContactOutcome(
                                    username=lead.instagram_username,
                                    phone=conflict_candidate.phone,
                                    role=_infer_role_from_candidate(conflict_candidate),
                                    label='',
                                    confidence=conflict_candidate.confidence,
                                    source_url=conflict_candidate.source_url,
                                    source_text=conflict_candidate.source_text[:200],
                                    reason=rejection_reason,
                                    action=action,
                                ),
                            )
                except Exception:
                    logger.exception(
                        'Failed to persist conflict candidates for username=%r',
                        lead.instagram_username,
                    )
                    raise
            else:
                for conflict_candidate in conflict_candidates:
                    stats.conflict_outcomes.append(
                        ConflictContactOutcome(
                            username=lead.instagram_username,
                            phone=conflict_candidate.phone,
                            role=_infer_role_from_candidate(conflict_candidate),
                            label='',
                            confidence=conflict_candidate.confidence,
                            source_url=conflict_candidate.source_url,
                            source_text=conflict_candidate.source_text[:200],
                            reason=rejection_reason,
                            action='dry-run',
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
