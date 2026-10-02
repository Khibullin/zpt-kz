"""Package 3 contact enrichment for one SellerLead.

This is not the legacy Instagram Brave pipeline in seller_lead_contact_search.
That function stays unchanged. Package 3 reads a seller's own website and may
use Google Places or Brave only to locate that website.
A Brave wa.me hit stays a pending candidate until the official website confirms it.
Google display name, address, and websiteUri are not website identity evidence.

Kolesa, Kaspi, OLX, Satu, 2GIS HTML and Instagram pages are not fetched.
Kolesa needs an official API or permission and has no HTTP client here.

Run journal: stdout of enrich_seller_contacts is enough. SellerLeadPipelineRun
belongs to the Instagram pipeline and is not written.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib import parse

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import (
    SellerLead,
    SellerLeadContactCandidate,
    SellerLeadEvidence,
    SellerLeadSource,
)
from core.services.seller_contact_google_places import (
    GooglePlaceLocator,
    GooglePlacesError,
    locate_google_place,
)
from core.services.seller_contact_website import (
    WebsiteCrawlResult,
    crawl_official_website,
    registrable_domain,
)
from core.services.seller_discovery_identity import (
    normalize_domain,
    normalize_instagram_identity,
    normalize_seller_phone,
    refresh_seller_lead_identity,
)
from core.services.seller_discovery_sources import (
    add_seller_lead_evidence,
    upsert_seller_lead_source,
)
from core.services.seller_lead_contact_search import normalize_kz_whatsapp_phone
from core.services.seller_lead_search import BraveSearchClient

logger = logging.getLogger(__name__)

SOURCE_WEBSITE = 'website'
SOURCE_GOOGLE = 'google_places'
SOURCE_BRAVE = 'brave'
ALLOWED_SOURCES = frozenset({SOURCE_WEBSITE, SOURCE_GOOGLE, SOURCE_BRAVE})
KOLESA_NOTE = 'kolesa requires official permission or API; Package 3 does not call it'
SKIPPED_BRAVE_HOSTS = frozenset({
    'kolesa.kz',
    'kaspi.kz',
    'olx.kz',
    'satu.kz',
    '2gis.kz',
    '2gis.com',
    'google.com',
    'google.kz',
    'instagram.com',
    'facebook.com',
    'youtube.com',
})
MAX_BRAVE_RESULTS = 5
MAX_WEBSITE_DOMAINS = 2


class SellerContactEnrichmentError(Exception):
    """Enrichment stopped before or during a controlled run."""


BRAVE_WHATSAPP_CANDIDATE_CONFIDENCE = 70


@dataclass
class EnrichmentObservation:
    field_name: str
    value: str
    confidence: int
    explicit_whatsapp: bool
    source_url: str
    excerpt: str
    confirms_whatsapp: bool = True


@dataclass
class SellerContactEnrichmentResult:
    dry_run: bool
    outcome: str
    observations: list[EnrichmentObservation] = field(default_factory=list)
    google_place_id: str = ''
    errors: list[str] = field(default_factory=list)
    wrote: bool = False


def enrich_seller_lead_contacts(
    seller_lead: SellerLead,
    *,
    sources: list[str] | tuple[str, ...] = (),
    dry_run: bool = True,
    urlopen: Callable[..., Any] | None = None,
    brave_client: BraveSearchClient | None = None,
) -> SellerContactEnrichmentResult:
    """Find public contacts for one lead. dry_run performs no database writes."""
    selected = _canonical_sources(sources)
    _require_flags(selected)
    observations: list[EnrichmentObservation] = []
    errors: list[str] = []
    website_urls: list[str] = []
    place_id = ''

    if seller_lead.website_url and SOURCE_WEBSITE in selected:
        website_urls.append(seller_lead.website_url)

    if SOURCE_GOOGLE in selected:
        try:
            locator = locate_google_place(
                name=seller_lead.name,
                city=seller_lead.city,
                address=_lead_address(seller_lead),
                website_url=seller_lead.website_url,
                urlopen=urlopen,
            )
        except GooglePlacesError as exc:
            raise SellerContactEnrichmentError(str(exc)) from None
        if locator.ambiguous:
            return SellerContactEnrichmentResult(
                dry_run=dry_run,
                outcome='ambiguous_google',
                errors=[locator.error],
            )
        place_id = locator.place_id
        if locator.website_uri and SOURCE_WEBSITE in selected:
            website_urls.append(locator.website_uri)
        elif locator.website_uri and SOURCE_WEBSITE not in selected:
            errors.append('websiteUri найден, но чтение сайта выключено.')

    if SOURCE_BRAVE in selected:
        brave_urls, brave_observations, brave_errors = _brave_locator(
            seller_lead,
            client=brave_client,
        )
        website_urls.extend(brave_urls)
        observations.extend(brave_observations)
        errors.extend(brave_errors)

    outcome = 'no_contacts'
    accepted_site = ''
    prior_website = (seller_lead.website_url or '').strip()
    prior_domain = registrable_domain(normalize_domain(prior_website)) if prior_website else ''
    if SOURCE_WEBSITE in selected:
        for website_url in _unique_domains(website_urls)[:MAX_WEBSITE_DOMAINS]:
            crawled = crawl_official_website(
                website_url,
                lead_name=seller_lead.name,
                city=seller_lead.city,
                address=_lead_address(seller_lead),
                phone=seller_lead.whatsapp,
                instagram=seller_lead.instagram_username,
                known_domain=prior_domain,
                domain_is_prior=bool(prior_website),
                urlopen=urlopen,
            )
            if crawled.outcome == 'ambiguous_website':
                outcome = 'ambiguous_website'
                continue
            if crawled.outcome != 'ok':
                errors.append(crawled.error or crawled.outcome)
                continue
            accepted_site = crawled.final_url
            observations.extend(_observations_from_website(crawled))
            outcome = 'no_contacts'
            break

    observations = _dedupe_observations(observations)
    if observations and outcome != 'ambiguous_website':
        outcome = 'enriched'
    if dry_run:
        return SellerContactEnrichmentResult(
            dry_run=True,
            outcome=outcome,
            observations=observations,
            google_place_id=place_id,
            errors=errors,
            wrote=False,
        )
    _apply(
        seller_lead,
        observations=observations,
        place_id=place_id,
        website_url=accepted_site,
    )
    return SellerContactEnrichmentResult(
        dry_run=False,
        outcome=outcome,
        observations=observations,
        google_place_id=place_id,
        errors=errors,
        wrote=True,
    )


def _canonical_sources(sources: list[str] | tuple[str, ...]) -> list[str]:
    if not sources:
        raise SellerContactEnrichmentError('Укажите источник: website, google_places или brave.')
    selected: list[str] = []
    for name in sources:
        key = str(name or '').strip().lower().replace('-', '_')
        if key == 'kolesa':
            raise SellerContactEnrichmentError(KOLESA_NOTE)
        if key not in ALLOWED_SOURCES:
            raise SellerContactEnrichmentError(f'Неизвестный источник enrichment: {name}')
        if key not in selected:
            selected.append(key)
    return selected


def _require_flags(sources: list[str]) -> None:
    if not bool(getattr(settings, 'SELLER_CONTACT_ENRICHMENT_ENABLED', False)):
        raise SellerContactEnrichmentError(
            'SELLER_CONTACT_ENRICHMENT_ENABLED=False. Обогащение контактов выключено.',
        )
    flag_by_source = {
        SOURCE_WEBSITE: 'SELLER_CONTACT_WEBSITE_ENABLED',
        SOURCE_GOOGLE: 'SELLER_CONTACT_GOOGLE_PLACES_ENABLED',
        SOURCE_BRAVE: 'SELLER_CONTACT_BRAVE_ENABLED',
    }
    for source in sources:
        flag = flag_by_source[source]
        if not bool(getattr(settings, flag, False)):
            raise SellerContactEnrichmentError(f'{flag}=False. Источник {source} выключен.')
    if SOURCE_GOOGLE in sources and not (getattr(settings, 'GOOGLE_PLACES_API_KEY', '') or '').strip():
        raise SellerContactEnrichmentError('GOOGLE_PLACES_API_KEY не задан.')
    if SOURCE_BRAVE in sources and not (getattr(settings, 'BRAVE_SEARCH_API_KEY', '') or '').strip():
        raise SellerContactEnrichmentError('BRAVE_SEARCH_API_KEY не задан.')


def _brave_locator(
    seller_lead: SellerLead,
    *,
    client: BraveSearchClient | None,
) -> tuple[list[str], list[EnrichmentObservation], list[str]]:
    api_key = (getattr(settings, 'BRAVE_SEARCH_API_KEY', '') or '').strip()
    search_client = client or BraveSearchClient(api_key=api_key)
    query = f'"{seller_lead.name}" {seller_lead.city} официальный сайт'.strip()
    logger.info('Seller contact Brave locator query=%r', query)
    try:
        rows = search_client.search(query, count=MAX_BRAVE_RESULTS)
    except Exception as exc:
        return [], [], [str(exc)[:300]]
    websites: list[str] = []
    observations: list[EnrichmentObservation] = []
    for row in rows:
        url = str(row.get('url') or '').strip()
        if not url:
            continue
        host = registrable_domain(parse.urlsplit(url).hostname or '')
        if host in SKIPPED_BRAVE_HOSTS:
            continue
        phone = _explicit_whatsapp_url_phone(url)
        if phone and _title_mentions_lead(str(row.get('title') or ''), seller_lead.name):
            observations.append(EnrichmentObservation(
                field_name='whatsapp',
                value=phone,
                confidence=BRAVE_WHATSAPP_CANDIDATE_CONFIDENCE,
                explicit_whatsapp=True,
                source_url=url[:500],
                excerpt='Brave search result',
                confirms_whatsapp=False,
            ))
            continue
        if url.startswith(('http://', 'https://')):
            websites.append(url)
    return websites, observations, []


def _title_mentions_lead(title: str, lead_name: str) -> bool:
    from core.services.seller_contact_website import distinctive_name_tokens
    from core.services.seller_discovery_identity import normalize_seller_name

    haystack = normalize_seller_name(title)
    tokens = distinctive_name_tokens(lead_name)
    return bool(tokens) and all(token in haystack for token in tokens[:2])


def _explicit_whatsapp_url_phone(url: str) -> str:
    from core.services.seller_contact_website import _phone_from_whatsapp_url

    return _phone_from_whatsapp_url(url)


def _observations_from_website(crawled: WebsiteCrawlResult) -> list[EnrichmentObservation]:
    observations = []
    for contact in crawled.contacts:
        if contact.field_name == 'whatsapp' and not contact.explicit_whatsapp:
            continue
        observations.append(EnrichmentObservation(
            field_name=contact.field_name,
            value=contact.value,
            confidence=contact.confidence,
            explicit_whatsapp=contact.explicit_whatsapp,
            source_url=crawled.final_url,
            excerpt=contact.excerpt,
        ))
    return observations


def _apply(
    seller_lead: SellerLead,
    *,
    observations: list[EnrichmentObservation],
    place_id: str,
    website_url: str,
) -> None:
    with transaction.atomic():
        locked = SellerLead.objects.select_for_update().get(pk=seller_lead.pk)
        if place_id:
            upsert_seller_lead_source(
                locked,
                source_type=SellerLeadSource.SOURCE_GOOGLE_PLACES,
                provider='google_places',
                external_id=place_id[:255],
                source_url='',
                display_name='',
                metadata={'role': 'locator'},
                observed_at=timezone.now(),
            )
        website_source = None
        if website_url:
            website_source = upsert_seller_lead_source(
                locked,
                source_type=SellerLeadSource.SOURCE_WEBSITE,
                provider='website',
                source_url=website_url[:500],
                display_name='',
                metadata={'identity': 'validated'},
                observed_at=timezone.now(),
            )
            if not locked.website_url:
                locked.website_url = website_url[:500]
        for observation in observations:
            source = website_source
            if not observation.confirms_whatsapp:
                source = upsert_seller_lead_source(
                    locked,
                    source_type=SellerLeadSource.SOURCE_WEB_SEARCH,
                    provider='brave',
                    source_url=observation.source_url[:500],
                    metadata={'kind': 'search_candidate'},
                    observed_at=timezone.now(),
                )
            _store_observation(locked, observation, source=source)
        refresh_seller_lead_identity(locked)
        seller_lead.refresh_from_db()


def _store_observation(
    lead: SellerLead,
    observation: EnrichmentObservation,
    *,
    source: SellerLeadSource | None,
) -> None:
    if observation.field_name == 'whatsapp':
        _store_whatsapp(lead, observation, source=source)
        return
    if observation.field_name == 'phone':
        _store_phone(lead, observation, source=source)
        return
    if observation.field_name == 'instagram':
        _store_instagram(lead, observation, source=source)


def _store_whatsapp(
    lead: SellerLead,
    observation: EnrichmentObservation,
    *,
    source: SellerLeadSource | None,
) -> None:
    if not observation.explicit_whatsapp:
        return
    number = normalize_kz_whatsapp_phone(observation.value)
    if not number:
        return
    conflict = _whatsapp_conflict(lead, number)
    confirmed = observation.confirms_whatsapp
    add_seller_lead_evidence(
        lead,
        field_name='whatsapp',
        value=number,
        source=source,
        confidence=observation.confidence,
        extraction_method=SellerLeadEvidence.METHOD_PARSER,
        is_selected=confirmed and not conflict and not _owner_verified_blocks(lead, 'whatsapp', number),
    )
    _upsert_candidate(
        lead,
        contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
        value=number,
        confidence=_candidate_confidence(observation.confidence),
        source_url=observation.source_url,
        source_text=observation.excerpt,
        source_type='wa_me' if 'wa.me' in observation.source_url or 'whatsapp' in observation.source_url else 'website',
        status=SellerLeadContactCandidate.STATUS_CONFLICT if conflict else SellerLeadContactCandidate.STATUS_PENDING,
    )
    if not confirmed or conflict or _owner_verified_blocks(lead, 'whatsapp', number):
        return
    if lead.whatsapp and lead.whatsapp != number:
        return
    if not lead.whatsapp:
        lead.whatsapp = number
        lead.whatsapp_confidence = _candidate_confidence(observation.confidence)
        lead.whatsapp_source_url = observation.source_url[:500]
        lead.whatsapp_source_text = observation.excerpt[:1000]
        lead.whatsapp_found_at = timezone.now()
        lead.save(update_fields=[
            'whatsapp',
            'whatsapp_confidence',
            'whatsapp_source_url',
            'whatsapp_source_text',
            'whatsapp_found_at',
            'updated_at',
        ])


def _store_phone(
    lead: SellerLead,
    observation: EnrichmentObservation,
    *,
    source: SellerLeadSource | None,
) -> None:
    number = normalize_seller_phone(observation.value)
    if not number:
        return
    add_seller_lead_evidence(
        lead,
        field_name='phone',
        value=number,
        source=source,
        confidence=observation.confidence,
        extraction_method=SellerLeadEvidence.METHOD_PARSER,
        is_selected=False,
    )
    if normalize_kz_whatsapp_phone(number):
        _upsert_candidate(
            lead,
            contact_type=SellerLeadContactCandidate.CONTACT_TYPE_PHONE,
            value=number,
            confidence=_candidate_confidence(observation.confidence),
            source_url=observation.source_url,
            source_text=observation.excerpt,
            source_type='website',
            status=SellerLeadContactCandidate.STATUS_PENDING,
        )


def _store_instagram(
    lead: SellerLead,
    observation: EnrichmentObservation,
    *,
    source: SellerLeadSource | None,
) -> None:
    username = normalize_instagram_identity(observation.value)
    if not username:
        return
    add_seller_lead_evidence(
        lead,
        field_name='instagram',
        value=username,
        source=source,
        confidence=observation.confidence,
        extraction_method=SellerLeadEvidence.METHOD_PARSER,
        is_selected=not lead.instagram_username or lead.instagram_username == username,
    )
    if lead.instagram_username and lead.instagram_username != username:
        return
    if not lead.instagram_username:
        lead.instagram_username = username
        try:
            with transaction.atomic():
                lead.save(update_fields=['instagram_username', 'updated_at'])
        except IntegrityError:
            lead.instagram_username = ''


def _whatsapp_conflict(lead: SellerLead, number: str) -> bool:
    current = normalize_kz_whatsapp_phone(lead.whatsapp) or ''
    return bool(current) and current != number


def _owner_verified_blocks(lead: SellerLead, field_name: str, number: str) -> bool:
    owner = SellerLeadEvidence.objects.filter(
        seller_lead=lead,
        field_name=field_name,
        is_owner_verified=True,
        is_selected=True,
    ).first()
    if owner is None:
        return False
    return (owner.normalized_value or owner.value) != number


def _upsert_candidate(
    lead: SellerLead,
    *,
    contact_type: str,
    value: str,
    confidence: str,
    source_url: str,
    source_text: str,
    source_type: str,
    status: str,
) -> None:
    existing = SellerLeadContactCandidate.objects.filter(
        seller_lead=lead,
        contact_type=contact_type,
        value=value,
    ).first()
    if existing is not None:
        if existing.status in {
            SellerLeadContactCandidate.STATUS_APPROVED,
            SellerLeadContactCandidate.STATUS_REJECTED,
        }:
            return
        existing.confidence = confidence
        existing.source_url = source_url[:500]
        existing.source_text = source_text[:400]
        existing.source_type = source_type
        existing.status = status
        existing.save(update_fields=[
            'confidence',
            'source_url',
            'source_text',
            'source_type',
            'status',
            'updated_at',
        ])
        return
    SellerLeadContactCandidate.objects.create(
        seller_lead=lead,
        contact_type=contact_type,
        value=value,
        role=SellerLeadContactCandidate.ROLE_SHOP,
        confidence=confidence,
        source_url=source_url[:500],
        source_text=source_text[:400],
        source_type=source_type,
        status=status,
        is_primary=False,
        found_at=timezone.now(),
    )


def _candidate_confidence(score: int) -> str:
    if score >= 90:
        return 'high'
    if score >= 70:
        return 'medium'
    return 'low'


def _lead_address(lead: SellerLead) -> str:
    location = getattr(lead, 'locations', None)
    if location is not None:
        primary = location.filter(is_primary=True).first() or location.first()
        if primary is not None and primary.address:
            return primary.address
    return ''


def _unique_domains(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for url in urls:
        host = registrable_domain(parse.urlsplit(url).hostname or '')
        if not host or host in seen or host in SKIPPED_BRAVE_HOSTS:
            continue
        seen.add(host)
        result.append(url)
    return result


def _dedupe_observations(observations: list[EnrichmentObservation]) -> list[EnrichmentObservation]:
    best: dict[tuple[str, str], EnrichmentObservation] = {}
    for item in observations:
        key = (item.field_name, item.value)
        current = best.get(key)
        if current is None or _observation_outranks(item, current):
            best[key] = item
    return list(best.values())


def _observation_outranks(item: EnrichmentObservation, current: EnrichmentObservation) -> bool:
    if item.confirms_whatsapp != current.confirms_whatsapp:
        return item.confirms_whatsapp
    return item.confidence > current.confidence

