from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib import error, parse, request

from django.conf import settings

from core.models import SellerLead

logger = logging.getLogger(__name__)

BRAVE_SEARCH_API_URL = 'https://api.search.brave.com/res/v1/web/search'
DEFAULT_SEARCH_TIMEOUT = 10.0

DEFAULT_SELLER_LEAD_CITIES = (
    'Алматы',
    'Астана',
    'Шымкент',
    'Караганда',
    'Актобе',
)

DEFAULT_SELLER_LEAD_CATEGORIES = (
    'автозапчасти',
    'авторазбор',
    'ходовая часть',
    'кузовные запчасти',
    'двигатели',
    'автоэлектрика',
    'оптика',
    'грузовые запчасти',
)

REJECTED_INSTAGRAM_PATH_MARKERS = frozenset({
    'p',
    'reel',
    'reels',
    'stories',
    'explore',
    'accounts',
    'direct',
    'tv',
})

INSTAGRAM_USERNAME_RE = re.compile(r'^[a-z0-9._]{1,30}$')
INSTAGRAM_HOSTS = frozenset({'instagram.com', 'www.instagram.com'})


class SellerLeadSearchError(Exception):
    """Базовая ошибка поиска потенциальных продавцов."""


class SellerLeadSearchConfigError(SellerLeadSearchError):
    """Ошибка конфигурации поиска."""


class SellerLeadSearchHTTPError(SellerLeadSearchError):
    """HTTP-ошибка поискового API."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class SellerLeadSearchTimeoutError(SellerLeadSearchError):
    """Таймаут поискового API."""


@dataclass(frozen=True)
class SearchResultItem:
    title: str
    url: str
    description: str


@dataclass(frozen=True)
class InstagramProfileCandidate:
    username: str
    profile_url: str
    title: str
    description: str
    source_url: str
    city: str
    category: str


@dataclass
class SellerLeadCollectStats:
    results_found: int = 0
    profiles_parsed: int = 0
    created: int = 0
    duplicates_skipped: int = 0
    links_rejected: int = 0
    errors: int = 0
    dry_run_profiles: list[InstagramProfileCandidate] = field(default_factory=list)


def build_search_queries(
    *,
    city: str | None = None,
    category: str | None = None,
    cities: tuple[str, ...] | list[str] | None = None,
    categories: tuple[str, ...] | list[str] | None = None,
) -> list[tuple[str, str, str]]:
    """
    Формирует поисковые запросы.

    Возвращает список кортежей (query, city, category).
    """
    selected_cities = [city] if city else list(cities or DEFAULT_SELLER_LEAD_CITIES)
    selected_categories = (
        [category] if category else list(categories or DEFAULT_SELLER_LEAD_CATEGORIES)
    )

    queries: list[tuple[str, str, str]] = []
    for category_name in selected_categories:
        for city_name in selected_cities:
            location = 'Казахстан' if category_name == 'грузовые запчасти' else city_name
            query = f'site:instagram.com {category_name} {location}'
            if category_name == 'автозапчасти' and city_name == 'Алматы':
                query = f'{query} WhatsApp'
            queries.append((query, city_name, category_name))
    return queries


def normalize_instagram_username(username: str) -> str:
    normalized = username.strip().lstrip('@').lower()
    if not INSTAGRAM_USERNAME_RE.fullmatch(normalized):
        return ''
    return normalized


def build_instagram_profile_url(username: str) -> str:
    return f'https://www.instagram.com/{username}/'


def parse_instagram_profile_url(url: str) -> dict[str, str] | None:
    """Извлекает username и нормализованную ссылку профиля Instagram."""
    if not url:
        return None

    parsed = parse.urlsplit(url.strip())
    host = (parsed.hostname or '').lower()
    if host not in INSTAGRAM_HOSTS:
        return None

    path = parse.unquote(parsed.path or '')
    segments = [segment for segment in path.split('/') if segment]
    if not segments:
        return None

    lowered_markers = {segment.lower() for segment in segments}
    if lowered_markers & REJECTED_INSTAGRAM_PATH_MARKERS:
        return None

    if len(segments) != 1:
        return None

    username = normalize_instagram_username(segments[0])
    if not username:
        return None

    return {
        'username': username,
        'profile_url': build_instagram_profile_url(username),
    }


def _parse_brave_response(payload: dict[str, Any]) -> list[SearchResultItem]:
    web_results = payload.get('web', {}).get('results', []) or []
    items: list[SearchResultItem] = []
    for row in web_results:
        if not isinstance(row, dict):
            continue
        title = str(row.get('title') or '').strip()
        result_url = str(row.get('url') or '').strip()
        description = str(row.get('description') or '').strip()
        if not result_url:
            continue
        items.append(SearchResultItem(title=title, url=result_url, description=description))
    return items


class BraveSearchClient:
    """Клиент Brave Search API."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = DEFAULT_SEARCH_TIMEOUT,
        urlopen: Callable[..., Any] | None = None,
    ):
        self.api_key = api_key.strip()
        self.timeout = timeout
        self._urlopen = urlopen or request.urlopen

    def search(self, query: str, *, count: int = 10) -> list[dict[str, str]]:
        if not self.api_key:
            raise SellerLeadSearchConfigError(
                'BRAVE_SEARCH_API_KEY не задан. Укажите ключ в переменных окружения.',
            )

        params = parse.urlencode({'q': query, 'count': max(1, min(count, 20))})
        request_url = f'{BRAVE_SEARCH_API_URL}?{params}'
        http_request = request.Request(
            request_url,
            headers={
                'Accept': 'application/json',
                'Accept-Encoding': 'gzip',
                'X-Subscription-Token': self.api_key,
            },
            method='GET',
        )

        logger.info('Brave search request for query=%r', query)

        try:
            with self._urlopen(http_request, timeout=self.timeout) as response:
                raw_body = response.read()
                status_code = getattr(response, 'status', 200)
        except error.HTTPError as exc:
            raise SellerLeadSearchHTTPError(
                f'Brave Search API HTTP {exc.code}',
                status_code=exc.code,
            ) from exc
        except error.URLError as exc:
            reason = getattr(exc, 'reason', exc)
            if 'timed out' in str(reason).lower():
                raise SellerLeadSearchTimeoutError('Brave Search API timeout') from exc
            raise SellerLeadSearchError(f'Brave Search API network error: {reason}') from exc

        if status_code == 429:
            raise SellerLeadSearchHTTPError('Brave Search API HTTP 429', status_code=429)
        if status_code >= 500:
            raise SellerLeadSearchHTTPError(
                f'Brave Search API HTTP {status_code}',
                status_code=status_code,
            )
        if status_code >= 400:
            raise SellerLeadSearchHTTPError(
                f'Brave Search API HTTP {status_code}',
                status_code=status_code,
            )

        try:
            payload = json.loads(raw_body.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SellerLeadSearchError('Brave Search API returned invalid JSON') from exc

        return [
            {
                'title': item.title,
                'url': item.url,
                'description': item.description,
            }
            for item in _parse_brave_response(payload)
        ]


def get_seller_search_settings() -> dict[str, Any]:
    provider = (getattr(settings, 'SELLER_SEARCH_PROVIDER', 'brave') or 'brave').strip().lower()
    api_key = (getattr(settings, 'BRAVE_SEARCH_API_KEY', '') or '').strip()
    enabled = bool(getattr(settings, 'SELLER_SEARCH_ENABLED', False))
    return {
        'provider': provider,
        'api_key': api_key,
        'enabled': enabled,
    }


def _seller_lead_exists(username: str, profile_url: str) -> bool:
    return SellerLead.objects.filter(instagram_username=username).exists() or SellerLead.objects.filter(
        instagram_url=profile_url,
    ).exists()


def collect_instagram_seller_leads(
    *,
    city: str | None = None,
    category: str | None = None,
    limit: int = 10,
    dry_run: bool = False,
    client: BraveSearchClient | None = None,
    search_settings: dict[str, Any] | None = None,
) -> SellerLeadCollectStats:
    settings_data = search_settings or get_seller_search_settings()
    stats = SellerLeadCollectStats()

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
    queries = build_search_queries(city=city, category=category)
    seen_usernames: set[str] = set()

    for query, query_city, query_category in queries:
        try:
            results = search_client.search(query, count=limit)
        except SellerLeadSearchError:
            stats.errors += 1
            logger.exception('Seller lead search failed for query=%r', query)
            continue

        stats.results_found += len(results)

        for result in results:
            profile = parse_instagram_profile_url(result['url'])
            if not profile:
                stats.links_rejected += 1
                continue

            username = profile['username']
            profile_url = profile['profile_url']
            stats.profiles_parsed += 1

            if username in seen_usernames:
                stats.duplicates_skipped += 1
                continue

            seen_usernames.add(username)

            if _seller_lead_exists(username, profile_url):
                stats.duplicates_skipped += 1
                continue

            candidate = InstagramProfileCandidate(
                username=username,
                profile_url=profile_url,
                title=result['title'],
                description=result['description'],
                source_url=result['url'],
                city=query_city,
                category=query_category,
            )

            if dry_run:
                stats.dry_run_profiles.append(candidate)
                continue

            display_name = result['title'].strip() or username
            SellerLead.objects.create(
                name=display_name[:255],
                instagram_username=username,
                instagram_url=profile_url,
                city=query_city,
                category=query_category,
                profile_description=result['description'],
                source_url=result['url'],
                source_type='web_search',
                status=SellerLead.STATUS_NEEDS_REVIEW,
            )
            stats.created += 1

    return stats
