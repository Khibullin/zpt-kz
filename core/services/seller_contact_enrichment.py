"""Contact enrichment for one SellerLead.

This is not the legacy Instagram Brave pipeline in seller_lead_contact_search.
That function stays unchanged. Official websites confirm WhatsApp. Google,
Yandex, Brave, and 2GIS may only locate that website or, for 2GIS, an explicit
WhatsApp contact type. A Brave wa.me hit stays pending until a verified source
confirms the same number. Google and Yandex content is not stored.

Kolesa, Kaspi, OLX, Satu, 2GIS HTML, Yandex Maps HTML, and Instagram pages are
not fetched. Kolesa needs an official API or permission and has no HTTP client.

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
    GooglePlacesError,
    locate_google_place,
)
from core.services.seller_contact_yandex import (
    YandexOrgError,
    locate_yandex_organization,
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
from core.services.seller_discovery_providers.base import DiscoveryProviderError
from core.services.seller_discovery_providers.catalog import resolve_city
from core.services.seller_discovery_providers.two_gis import (
    TwoGisPlacesClient,
    _phones_from_item,
    _website_and_instagram_from_contacts,
)
from core.services.seller_lead_contact_search import normalize_kz_whatsapp_phone
from core.services.seller_lead_search import BraveSearchClient

logger = logging.getLogger(__name__)

SOURCE_WEBSITE = 'website'
SOURCE_GOOGLE = 'google_places'
SOURCE_BRAVE = 'brave'
SOURCE_TWO_GIS = 'two_gis'
SOURCE_YANDEX = 'yandex_org'
SOURCE_ALL = 'all'
ALLOWED_SOURCES = frozenset({
    SOURCE_WEBSITE,
    SOURCE_GOOGLE,
    SOURCE_BRAVE,
    SOURCE_TWO_GIS,
    SOURCE_YANDEX,
    SOURCE_ALL,
})
SOURCE_ORDER = (
    SOURCE_WEBSITE,
    SOURCE_TWO_GIS,
    SOURCE_GOOGLE,
    SOURCE_BRAVE,
    SOURCE_YANDEX,
)
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
    'yandex.ru',
    'yandex.kz',
    'yandex.com',
    'maps.yandex.ru',
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
    origin: str = ''
    conflicting: bool = False


@dataclass
class SourceRun:
    source: str
    status: str
    detail: str = ''


@dataclass
class LocatorHit:
    source: str
    website_url: str = ''
    detail: str = ''


@dataclass
class SellerContactEnrichmentResult:
    dry_run: bool
    outcome: str
    observations: list[EnrichmentObservation] = field(default_factory=list)
    google_place_id: str = ''
    errors: list[str] = field(default_factory=list)
    wrote: bool = False
    source_runs: list[SourceRun] = field(default_factory=list)
    locators: list[LocatorHit] = field(default_factory=list)
    websites_considered: list[str] = field(default_factory=list)
    verified_whatsapp: list[str] = field(default_factory=list)
    pending_candidates: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


def enrich_seller_lead_contacts(
    seller_lead: SellerLead,
    *,
    sources: list[str] | tuple[str, ...] = (),
    dry_run: bool = True,
    urlopen: Callable[..., Any] | None = None,
    brave_client: BraveSearchClient | None = None,
    stop_on_verified_whatsapp: bool = False,
) -> SellerContactEnrichmentResult:
    """Find public contacts for one lead. dry_run performs no database writes."""
    active, source_runs, all_mode = _prepare_sources(sources)
    observations: list[EnrichmentObservation] = []
    errors: list[str] = []
    locators: list[LocatorHit] = []
    website_urls: list[str] = []
    place_id = ''
    two_gis_external_id = ''
    outcome = 'no_contacts'
    accepted_site = ''
    websites_considered: list[str] = []
    crawled_domains: set[str] = set()
    prior_website = (seller_lead.website_url or '').strip()
    prior_domain = registrable_domain(normalize_domain(prior_website)) if prior_website else ''

    if SOURCE_WEBSITE in active and prior_website:
        website_urls.append(prior_website)
        locators.append(LocatorHit(source='existing_website', website_url=prior_website))
        accepted_site, outcome = _crawl_candidates(
            seller_lead,
            website_urls,
            observations=observations,
            errors=errors,
            websites_considered=websites_considered,
            crawled_domains=crawled_domains,
            prior_domain=prior_domain,
            prior_website=bool(prior_website),
            urlopen=urlopen,
            accepted_site=accepted_site,
            outcome=outcome,
        )
        source_runs.append(SourceRun(SOURCE_WEBSITE, 'executed'))

    verified_now = _verified_numbers(observations)
    skip_later = stop_on_verified_whatsapp and len(verified_now) == 1
    later = [source for source in active if source != SOURCE_WEBSITE]

    if skip_later:
        for source in later:
            source_runs.append(SourceRun(source, 'skipped_verified_whatsapp'))
    else:
        if SOURCE_TWO_GIS in active:
            external_id, urls, two_gis_observations, two_gis_errors = _two_gis_locator(
                seller_lead,
                urlopen=urlopen,
            )
            two_gis_external_id = external_id
            website_urls.extend(urls)
            observations.extend(two_gis_observations)
            errors.extend(two_gis_errors)
            for url in urls:
                locators.append(LocatorHit(source=SOURCE_TWO_GIS, website_url=url))
            if two_gis_errors == ['contacts_disabled']:
                status = 'contacts_disabled'
            elif two_gis_errors and not urls and not two_gis_observations:
                status = 'error'
            else:
                status = 'executed'
            source_runs.append(SourceRun(SOURCE_TWO_GIS, status, '; '.join(two_gis_errors)[:300]))

        if SOURCE_GOOGLE in active:
            try:
                locator = locate_google_place(
                    name=seller_lead.name,
                    city=seller_lead.city,
                    address=_lead_address(seller_lead),
                    website_url=seller_lead.website_url,
                    urlopen=urlopen,
                )
            except GooglePlacesError as exc:
                if all_mode:
                    errors.append(str(exc))
                    source_runs.append(SourceRun(SOURCE_GOOGLE, 'error', str(exc)[:300]))
                    locator = None
                else:
                    raise SellerContactEnrichmentError(str(exc)) from None
            if locator is not None and locator.ambiguous:
                if active == [SOURCE_GOOGLE]:
                    return _result(
                        dry_run=dry_run,
                        outcome='ambiguous_google',
                        observations=[],
                        errors=[locator.error],
                        source_runs=source_runs + [SourceRun(SOURCE_GOOGLE, 'ambiguous', locator.error)],
                        wrote=False,
                    )
                errors.append(locator.error or 'неоднозначное совпадение Google')
                source_runs.append(SourceRun(SOURCE_GOOGLE, 'ambiguous', locator.error))
            elif locator is not None:
                place_id = locator.place_id
                if locator.website_uri and SOURCE_WEBSITE in active:
                    website_urls.append(locator.website_uri)
                    locators.append(LocatorHit(source=SOURCE_GOOGLE, website_url=locator.website_uri))
                elif locator.website_uri:
                    errors.append('websiteUri найден, но чтение сайта выключено.')
                source_runs.append(SourceRun(SOURCE_GOOGLE, 'executed'))

        if SOURCE_BRAVE in active:
            brave_urls, brave_observations, brave_errors = _brave_locator(
                seller_lead,
                client=brave_client,
            )
            website_urls.extend(brave_urls)
            observations.extend(brave_observations)
            errors.extend(brave_errors)
            for url in brave_urls:
                locators.append(LocatorHit(source=SOURCE_BRAVE, website_url=url))
            status = 'error' if brave_errors and not brave_urls and not brave_observations else 'executed'
            source_runs.append(SourceRun(SOURCE_BRAVE, status, '; '.join(brave_errors)[:300]))

        if SOURCE_YANDEX in active:
            try:
                yandex = locate_yandex_organization(
                    name=seller_lead.name,
                    city=seller_lead.city,
                    urlopen=urlopen,
                )
            except YandexOrgError as exc:
                if all_mode:
                    errors.append(str(exc))
                    source_runs.append(SourceRun(SOURCE_YANDEX, 'error', str(exc)[:300]))
                    yandex = None
                else:
                    raise SellerContactEnrichmentError(str(exc)) from None
            if yandex is not None and yandex.ambiguous:
                errors.append(yandex.error or 'неоднозначное совпадение Yandex')
                source_runs.append(SourceRun(SOURCE_YANDEX, 'ambiguous', yandex.error))
            elif yandex is not None:
                if yandex.website_uri and SOURCE_WEBSITE in active:
                    website_urls.append(yandex.website_uri)
                    locators.append(LocatorHit(source=SOURCE_YANDEX, website_url=yandex.website_uri))
                source_runs.append(SourceRun(SOURCE_YANDEX, 'executed', yandex.error))

    if SOURCE_WEBSITE in active and not skip_later:
        accepted_site, outcome = _crawl_candidates(
            seller_lead,
            website_urls,
            observations=observations,
            errors=errors,
            websites_considered=websites_considered,
            crawled_domains=crawled_domains,
            prior_domain=prior_domain,
            prior_website=bool(prior_website),
            urlopen=urlopen,
            accepted_site=accepted_site,
            outcome=outcome,
        )
        if not any(run.source == SOURCE_WEBSITE for run in source_runs):
            source_runs.append(SourceRun(SOURCE_WEBSITE, 'executed'))

    _annotate_locator_agreement(locators)
    observations, outcome, verified, pending, conflicts = _classify_observations(observations, outcome)
    if dry_run:
        return _result(
            dry_run=True,
            outcome=outcome,
            observations=observations,
            google_place_id=place_id,
            errors=errors,
            source_runs=source_runs,
            locators=locators,
            websites_considered=websites_considered,
            verified_whatsapp=verified,
            pending_candidates=pending,
            conflicts=conflicts,
            wrote=False,
        )
    _apply(
        seller_lead,
        observations=observations,
        place_id=place_id,
        website_url=accepted_site,
        two_gis_external_id=two_gis_external_id,
    )
    return _result(
        dry_run=False,
        outcome=outcome,
        observations=observations,
        google_place_id=place_id,
        errors=errors,
        source_runs=source_runs,
        locators=locators,
        websites_considered=websites_considered,
        verified_whatsapp=verified,
        pending_candidates=pending,
        conflicts=conflicts,
        wrote=True,
    )


def _canonical_sources(sources: list[str] | tuple[str, ...]) -> tuple[list[str], bool]:
    if not sources:
        raise SellerContactEnrichmentError(
            'Укажите источник: website, google_places, brave, two_gis, yandex_org или all.',
        )
    aliases = {'2gis': SOURCE_TWO_GIS, 'yandex': SOURCE_YANDEX}
    all_mode = False
    requested: list[str] = []
    for name in sources:
        raw = str(name or '').strip().lower().replace('-', '_')
        key = aliases.get(raw, raw)
        if key == 'kolesa':
            raise SellerContactEnrichmentError(KOLESA_NOTE)
        if key == SOURCE_ALL:
            all_mode = True
            continue
        if key not in SOURCE_ORDER:
            raise SellerContactEnrichmentError(f'Неизвестный источник enrichment: {name}')
        if key not in requested:
            requested.append(key)
    if all_mode:
        return list(SOURCE_ORDER), True
    if not requested:
        raise SellerContactEnrichmentError(
            'Укажите источник: website, google_places, brave, two_gis, yandex_org или all.',
        )
    return [source for source in SOURCE_ORDER if source in requested], False


def _prepare_sources(sources: list[str] | tuple[str, ...]) -> tuple[list[str], list[SourceRun], bool]:
    requested, all_mode = _canonical_sources(sources)
    if not bool(getattr(settings, 'SELLER_CONTACT_ENRICHMENT_ENABLED', False)):
        raise SellerContactEnrichmentError(
            'SELLER_CONTACT_ENRICHMENT_ENABLED=False. Обогащение контактов выключено.',
        )
    active: list[str] = []
    runs: list[SourceRun] = []
    for source in requested:
        problem = _source_problem(source)
        if not problem:
            active.append(source)
            continue
        if not all_mode:
            raise SellerContactEnrichmentError(problem)
        status = 'skipped_missing_key' if 'не задан' in problem else 'skipped_disabled'
        runs.append(SourceRun(source, status, problem))
    return active, runs, all_mode


def _source_problem(source: str) -> str:
    flag_by_source = {
        SOURCE_WEBSITE: 'SELLER_CONTACT_WEBSITE_ENABLED',
        SOURCE_GOOGLE: 'SELLER_CONTACT_GOOGLE_PLACES_ENABLED',
        SOURCE_BRAVE: 'SELLER_CONTACT_BRAVE_ENABLED',
        SOURCE_TWO_GIS: 'SELLER_CONTACT_2GIS_ENABLED',
        SOURCE_YANDEX: 'SELLER_CONTACT_YANDEX_ENABLED',
    }
    key_by_source = {
        SOURCE_GOOGLE: 'GOOGLE_PLACES_API_KEY',
        SOURCE_BRAVE: 'BRAVE_SEARCH_API_KEY',
        SOURCE_TWO_GIS: 'TWO_GIS_API_KEY',
        SOURCE_YANDEX: 'YANDEX_ORG_SEARCH_API_KEY',
    }
    flag = flag_by_source[source]
    if not bool(getattr(settings, flag, False)):
        return f'{flag}=False. Источник {source} выключен.'
    key_name = key_by_source.get(source, '')
    if key_name and not (getattr(settings, key_name, '') or '').strip():
        return f'{key_name} не задан.'
    return ''


def _brave_queries(seller_lead: SellerLead) -> list[str]:
    name = seller_lead.name
    city = seller_lead.city
    return [
        f'"{name}" {city} WhatsApp'.strip(),
        f'"{name}" {city} контакты'.strip(),
        f'"{name}" wa.me'.strip(),
        f'"{name}" {city} официальный сайт'.strip(),
    ]


def _brave_locator(
    seller_lead: SellerLead,
    *,
    client: BraveSearchClient | None,
) -> tuple[list[str], list[EnrichmentObservation], list[str]]:
    api_key = (getattr(settings, 'BRAVE_SEARCH_API_KEY', '') or '').strip()
    search_client = client or BraveSearchClient(api_key=api_key)
    websites: list[str] = []
    observations: list[EnrichmentObservation] = []
    errors: list[str] = []
    seen_urls: set[str] = set()
    for query in _brave_queries(seller_lead):
        logger.info('Seller contact Brave locator query=%r', query)
        try:
            rows = search_client.search(query, count=MAX_BRAVE_RESULTS)
        except Exception as exc:
            errors.append(str(exc)[:300])
            continue
        for row in rows:
            url = str(row.get('url') or '').strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
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
                    origin=SOURCE_BRAVE,
                ))
                continue
            if url.startswith(('http://', 'https://')):
                websites.append(url)
    return websites, observations, errors


def _crawl_candidates(
    seller_lead: SellerLead,
    website_urls: list[str],
    *,
    observations: list[EnrichmentObservation],
    errors: list[str],
    websites_considered: list[str],
    crawled_domains: set[str],
    prior_domain: str,
    prior_website: bool,
    urlopen: Callable[..., Any] | None,
    accepted_site: str,
    outcome: str,
) -> tuple[str, str]:
    for website_url in _unique_domains(website_urls):
        host = registrable_domain(parse.urlsplit(website_url).hostname or '')
        if not host or host in crawled_domains:
            continue
        if len(crawled_domains) >= MAX_WEBSITE_DOMAINS:
            break
        crawled_domains.add(host)
        websites_considered.append(website_url)
        is_prior = prior_website and host == prior_domain
        crawled = crawl_official_website(
            website_url,
            lead_name=seller_lead.name,
            city=seller_lead.city,
            address=_lead_address(seller_lead),
            phone=seller_lead.whatsapp,
            instagram=seller_lead.instagram_username,
            known_domain=prior_domain if is_prior else '',
            domain_is_prior=is_prior,
            urlopen=urlopen,
        )
        if crawled.outcome == 'ambiguous_website':
            outcome = 'ambiguous_website'
            continue
        if crawled.outcome != 'ok':
            errors.append(crawled.error or crawled.outcome)
            continue
        if not accepted_site:
            accepted_site = crawled.final_url
        observations.extend(_observations_from_website(crawled))
        outcome = 'no_contacts'
        if _verified_numbers(observations):
            break
    return accepted_site, outcome


def _two_gis_locator(
    seller_lead: SellerLead,
    *,
    urlopen: Callable[..., Any] | None,
) -> tuple[str, list[str], list[EnrichmentObservation], list[str]]:
    """Official 2GIS catalog API. HTML pages are not fetched."""
    if not bool(getattr(settings, 'SELLER_DISCOVERY_2GIS_CONTACTS_ENABLED', False)):
        return '', [], [], ['contacts_disabled']
    try:
        city = resolve_city(seller_lead.city)
    except DiscoveryProviderError as exc:
        return '', [], [], [str(exc)]
    client = TwoGisPlacesClient(
        api_key=(getattr(settings, 'TWO_GIS_API_KEY', '') or '').strip(),
        urlopen=urlopen,
    )
    try:
        items = client.search_items(
            seller_lead.name,
            city=city,
            page_size=5,
            max_pages=1,
        )
    except DiscoveryProviderError as exc:
        return '', [], [], [str(exc)]
    matches = [item for item in items if _two_gis_name_matches(item, seller_lead.name)]
    if len(matches) != 1:
        if len(matches) > 1:
            return '', [], [], ['неоднозначное совпадение 2GIS']
        return '', [], [], []
    item = matches[0]
    external_id = str(item.get('id') or '')[:255]
    website, instagram = _website_and_instagram_from_contacts(item)
    phones, whatsapp = _phones_from_item(item)
    observations: list[EnrichmentObservation] = []
    if whatsapp:
        observations.append(EnrichmentObservation(
            field_name='whatsapp',
            value=whatsapp,
            confidence=90,
            explicit_whatsapp=True,
            source_url='',
            excerpt='2GIS contact type whatsapp',
            confirms_whatsapp=True,
            origin=SOURCE_TWO_GIS,
        ))
    for phone in phones:
        if phone == whatsapp:
            continue
        observations.append(EnrichmentObservation(
            field_name='phone',
            value=phone,
            confidence=80,
            explicit_whatsapp=False,
            source_url='',
            excerpt='2GIS contact type phone',
            confirms_whatsapp=True,
            origin=SOURCE_TWO_GIS,
        ))
    if instagram:
        observations.append(EnrichmentObservation(
            field_name='instagram',
            value=instagram,
            confidence=85,
            explicit_whatsapp=False,
            source_url=instagram[:500],
            excerpt='2GIS contact type instagram',
            confirms_whatsapp=True,
            origin=SOURCE_TWO_GIS,
        ))
    return external_id, ([website] if website else []), observations, []


def _two_gis_name_matches(item: dict[str, Any], lead_name: str) -> bool:
    from core.services.seller_contact_website import distinctive_name_tokens
    from core.services.seller_discovery_identity import normalize_seller_name

    tokens = distinctive_name_tokens(lead_name)
    if len(tokens) < 2:
        return False
    haystack = normalize_seller_name(str(item.get('name') or ''))
    matched = [token for token in tokens if token in haystack]
    return len(matched) >= 2


def _verified_numbers(observations: list[EnrichmentObservation]) -> list[str]:
    numbers: list[str] = []
    for item in observations:
        if item.field_name != 'whatsapp' or not item.confirms_whatsapp or not item.explicit_whatsapp:
            continue
        if item.conflicting:
            continue
        number = normalize_kz_whatsapp_phone(item.value)
        if number and number not in numbers:
            numbers.append(number)
    return numbers


def _classify_observations(
    observations: list[EnrichmentObservation],
    outcome: str,
) -> tuple[list[EnrichmentObservation], str, list[str], list[str], list[str]]:
    observations = _dedupe_observations(observations)
    verified = _verified_numbers(observations)
    if len(verified) > 1:
        verified_set = set(verified)
        for item in observations:
            number = normalize_kz_whatsapp_phone(item.value) if item.field_name == 'whatsapp' else ''
            if item.confirms_whatsapp and item.explicit_whatsapp and number in verified_set:
                item.conflicting = True
        outcome = 'conflict'
        verified = []
    elif observations and outcome != 'ambiguous_website':
        outcome = 'enriched'
    pending: list[str] = []
    conflicts: list[str] = []
    for item in observations:
        if item.field_name != 'whatsapp':
            continue
        number = normalize_kz_whatsapp_phone(item.value)
        if not number:
            continue
        if item.conflicting:
            if number not in conflicts:
                conflicts.append(number)
        elif not item.confirms_whatsapp and number not in pending and number not in verified:
            pending.append(number)
    return observations, outcome, verified, pending, conflicts


def _annotate_locator_agreement(locators: list[LocatorHit]) -> None:
    by_domain: dict[str, set[str]] = {}
    for hit in locators:
        host = registrable_domain(parse.urlsplit(hit.website_url).hostname or '') if hit.website_url else ''
        if host:
            by_domain.setdefault(host, set()).add(hit.source)
    for hit in locators:
        host = registrable_domain(parse.urlsplit(hit.website_url).hostname or '') if hit.website_url else ''
        agreed = by_domain.get(host) or set()
        if len(agreed) >= 2:
            hit.detail = f"agreement={len(agreed)}:{','.join(sorted(agreed))}"


def _result(**kwargs: Any) -> SellerContactEnrichmentResult:
    return SellerContactEnrichmentResult(**kwargs)


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
            origin=SOURCE_WEBSITE,
        ))
    return observations


def _apply(
    seller_lead: SellerLead,
    *,
    observations: list[EnrichmentObservation],
    place_id: str,
    website_url: str,
    two_gis_external_id: str = '',
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
        two_gis_source = None
        if two_gis_external_id:
            two_gis_source = upsert_seller_lead_source(
                locked,
                source_type=SellerLeadSource.SOURCE_TWO_GIS,
                provider='two_gis',
                external_id=two_gis_external_id[:255],
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
            if observation.origin == SOURCE_TWO_GIS:
                source = two_gis_source
            elif observation.origin == SOURCE_BRAVE or not observation.confirms_whatsapp:
                source = upsert_seller_lead_source(
                    locked,
                    source_type=SellerLeadSource.SOURCE_WEB_SEARCH,
                    provider='brave',
                    source_url=observation.source_url[:500],
                    metadata={'kind': 'search_candidate'},
                    observed_at=timezone.now(),
                )
            else:
                source = website_source
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
    conflict = observation.conflicting or _whatsapp_conflict(lead, number)
    confirmed = observation.confirms_whatsapp and not observation.conflicting
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
        source_type=(
            'other' if observation.origin == SOURCE_TWO_GIS
            else 'wa_me' if 'wa.me' in observation.source_url or 'whatsapp' in observation.source_url
            else 'website'
        ),
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
        key = (item.field_name, item.value, item.origin)
        current = best.get(key)
        if current is None or _observation_outranks(item, current):
            best[key] = item
    return list(best.values())


def _observation_outranks(item: EnrichmentObservation, current: EnrichmentObservation) -> bool:
    if item.confirms_whatsapp != current.confirms_whatsapp:
        return item.confirms_whatsapp
    return item.confidence > current.confidence

