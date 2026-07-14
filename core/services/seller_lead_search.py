from __future__ import annotations

import gzip
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
BODY_PREVIEW_LIMIT = 400

HTTP_STATUS_MESSAGES = {
    401: 'Brave Search API HTTP 401: invalid or missing API key',
    403: 'Brave Search API HTTP 403: access denied',
    422: 'Brave Search API HTTP 422: invalid request parameters',
    429: 'Brave Search API HTTP 429: rate limit exceeded',
}

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


@dataclass(frozen=True)
class BraveSearchResponseInfo:
    status_code: int
    content_type: str
    content_encoding: str
    body_length: int


@dataclass(frozen=True)
class DryRunResultDetail:
    title: str
    url: str
    username: str
    accepted: bool
    reason: str


@dataclass
class SellerLeadCollectStats:
    queries_executed: int = 0
    results_found: int = 0
    profiles_parsed: int = 0
    created: int = 0
    created_lead_ids: list[int] = field(default_factory=list)
    duplicates_skipped: int = 0
    links_rejected: int = 0
    errors: int = 0
    dry_run_profiles: list[InstagramProfileCandidate] = field(default_factory=list)
    dry_run_result_details: list[DryRunResultDetail] = field(default_factory=list)
    api_response_info: BraveSearchResponseInfo | None = None


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


def explain_instagram_url_rejection(url: str) -> str:
    if not url:
        return 'пустой URL'
    parsed = parse.urlsplit(url.strip())
    host = (parsed.hostname or '').lower()
    if host not in INSTAGRAM_HOSTS:
        return 'не домен instagram.com'
    path = parse.unquote(parsed.path or '')
    segments = [segment for segment in path.split('/') if segment]
    if not segments:
        return 'путь Instagram без username'
    lowered_markers = {segment.lower() for segment in segments}
    rejected_markers = lowered_markers & REJECTED_INSTAGRAM_PATH_MARKERS
    if rejected_markers:
        return f'служебный путь Instagram ({", ".join(sorted(rejected_markers))})'
    if len(segments) != 1:
        return 'путь содержит несколько сегментов, не профиль'
    username = normalize_instagram_username(segments[0])
    if not username:
        return 'недопустимый username Instagram'
    return 'не распознан как профиль Instagram'


INVALID_BRAVE_API_KEY_MESSAGE = (
    'BRAVE_SEARCH_API_KEY содержит недопустимые символы. '
    'Скопируйте ключ заново из Brave Search API Dashboard.'
)


def _clean_api_key_value(api_key: str) -> str:
    cleaned = api_key.strip().lstrip('\ufeff').strip('\r\n')
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in '"\'':
        cleaned = cleaned[1:-1].strip()
    return cleaned


def api_key_has_internal_whitespace(api_key: str) -> bool:
    return any(character in api_key for character in ' \t\n\r')


def get_api_key_validation_metadata(api_key: str) -> dict[str, bool | int]:
    cleaned = _clean_api_key_value(api_key)
    return {
        'loaded': bool(cleaned),
        'is_ascii': cleaned.isascii() if cleaned else False,
        'has_internal_whitespace': api_key_has_internal_whitespace(cleaned) if cleaned else False,
        'length': len(cleaned),
    }


def _sanitize_api_key(api_key: str) -> str:
    if not api_key:
        raise SellerLeadSearchConfigError(
            'BRAVE_SEARCH_API_KEY не задан. Укажите ключ в переменных окружения.',
        )
    cleaned = _clean_api_key_value(api_key)
    if not cleaned:
        raise SellerLeadSearchConfigError(
            'BRAVE_SEARCH_API_KEY не задан. Укажите ключ в переменных окружения.',
        )
    if not cleaned.isascii():
        raise SellerLeadSearchConfigError(INVALID_BRAVE_API_KEY_MESSAGE)
    if api_key_has_internal_whitespace(cleaned):
        raise SellerLeadSearchConfigError(INVALID_BRAVE_API_KEY_MESSAGE)
    return cleaned


def _build_brave_search_url(query: str, count: int) -> str:
    params = {'q': query, 'count': max(1, min(count, 20))}
    request_url = f'{BRAVE_SEARCH_API_URL}?{parse.urlencode(params, encoding="utf-8")}'
    if not request_url.isascii():
        raise SellerLeadSearchConfigError(
            'Не удалось безопасно закодировать URL запроса Brave Search API.',
        )
    return request_url


def _urlopen_without_proxy(http_request: request.Request, timeout: float):
    opener = request.build_opener(request.ProxyHandler({}))
    return opener.open(http_request, timeout=timeout)


def _get_response_header(headers: Any, name: str) -> str:
    if headers is None:
        return ''
    value = headers.get(name) if hasattr(headers, 'get') else ''
    if not value and hasattr(headers, 'get_all'):
        values = headers.get_all(name) or headers.get_all(name.lower()) or []
        value = values[0] if values else ''
    if not value and isinstance(headers, dict):
        value = headers.get(name) or headers.get(name.lower()) or ''
    return str(value).split(';', 1)[0].strip()


def _redact_secrets(text: str, *, api_key: str) -> str:
    redacted = text
    if api_key:
        redacted = redacted.replace(api_key, '[REDACTED]')
    for marker in ('X-Subscription-Token', 'Authorization', 'Cookie'):
        redacted = re.sub(
            rf'({re.escape(marker)}\s*[:=]\s*)(\S+)',
            r'\1[REDACTED]',
            redacted,
            flags=re.IGNORECASE,
        )
    return redacted


def _safe_body_preview(body: bytes, *, api_key: str = '', limit: int = BODY_PREVIEW_LIMIT) -> str:
    if not body:
        return ''
    text = body.decode('utf-8', errors='replace')
    return _redact_secrets(text, api_key=api_key)[:limit]


def _decode_response_body(raw_body: bytes, content_encoding: str) -> bytes:
    encoding = (content_encoding or '').strip().lower()
    is_gzip = encoding == 'gzip' or (len(raw_body) >= 2 and raw_body[:2] == b'\x1f\x8b')
    if not is_gzip:
        return raw_body
    try:
        return gzip.decompress(raw_body)
    except OSError:
        return raw_body


def _extract_http_error_detail(raw_body: bytes, *, api_key: str, content_encoding: str = '') -> str:
    decoded_body = _decode_response_body(raw_body, content_encoding)
    if not decoded_body:
        return ''
    try:
        payload = json.loads(decoded_body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _safe_body_preview(decoded_body, api_key=api_key)
    if not isinstance(payload, dict):
        return _safe_body_preview(decoded_body, api_key=api_key)
    for key in ('message', 'error', 'detail', 'title'):
        value = payload.get(key)
        if value:
            return _redact_secrets(str(value), api_key=api_key)
    return _safe_body_preview(decoded_body, api_key=api_key)


def _raise_for_http_status(
    status_code: int,
    raw_body: bytes,
    headers: Any,
    *,
    api_key: str,
) -> None:
    content_encoding = _get_response_header(headers, 'Content-Encoding')
    detail = _extract_http_error_detail(raw_body, api_key=api_key, content_encoding=content_encoding)
    message = HTTP_STATUS_MESSAGES.get(status_code, f'Brave Search API HTTP {status_code}')
    if detail:
        message = f'{message}: {detail}'
    raise SellerLeadSearchHTTPError(message, status_code=status_code)


def _parse_json_response(
    raw_body: bytes,
    *,
    status_code: int,
    content_type: str,
    content_encoding: str,
    api_key: str,
) -> dict[str, Any]:
    if not raw_body:
        raise SellerLeadSearchError(
            'Brave Search API returned empty body: '
            f'status={status_code}, content_type={content_type or "unknown"}',
        )

    decoded_body = _decode_response_body(raw_body, content_encoding)
    content_type_lower = (content_type or '').lower()
    looks_like_json = decoded_body.lstrip().startswith((b'{', b'['))
    if 'json' not in content_type_lower and not looks_like_json:
        preview = _safe_body_preview(decoded_body, api_key=api_key)
        raise SellerLeadSearchError(
            'Brave Search API returned invalid JSON: '
            f'status={status_code}, content_type={content_type or "unknown"}, '
            f'body_preview={preview!r}',
        )

    try:
        text = decoded_body.decode('utf-8')
    except UnicodeDecodeError as exc:
        preview = _safe_body_preview(decoded_body, api_key=api_key)
        raise SellerLeadSearchError(
            'Brave Search API returned invalid JSON: '
            f'status={status_code}, content_type={content_type or "unknown"}, '
            f'body_preview={preview!r}',
        ) from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        preview = _safe_body_preview(decoded_body, api_key=api_key)
        raise SellerLeadSearchError(
            'Brave Search API returned invalid JSON: '
            f'status={status_code}, content_type={content_type or "unknown"}, '
            f'body_preview={preview!r}',
        ) from exc

    if not isinstance(payload, dict):
        raise SellerLeadSearchError(
            f'Brave Search API returned unexpected JSON payload: status={status_code}',
        )
    return payload


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
        self.api_key = _sanitize_api_key(api_key)
        self.timeout = timeout
        self._urlopen = urlopen or _urlopen_without_proxy
        self.last_response_info: BraveSearchResponseInfo | None = None

    def search(self, query: str, *, count: int = 10) -> list[dict[str, str]]:
        if not self.api_key:
            raise SellerLeadSearchConfigError(
                'BRAVE_SEARCH_API_KEY не задан. Укажите ключ в переменных окружения.',
            )

        request_url = _build_brave_search_url(query, count)
        http_request = request.Request(
            request_url,
            headers={
                'Accept': 'application/json',
                'X-Subscription-Token': self.api_key,
            },
            method='GET',
        )

        logger.info('Brave search request for query=%r', query)

        try:
            with self._urlopen(http_request, timeout=self.timeout) as response:
                raw_body = response.read()
                status_code = getattr(response, 'status', 200)
                headers = response.headers
        except error.HTTPError as exc:
            raw_body = exc.read() if exc.fp else b''
            status_code = exc.code
            headers = exc.headers
            if status_code >= 400:
                _raise_for_http_status(status_code, raw_body, headers, api_key=self.api_key)
            raise SellerLeadSearchHTTPError(
                f'Brave Search API HTTP {status_code}',
                status_code=status_code,
            ) from exc
        except error.URLError as exc:
            reason = getattr(exc, 'reason', exc)
            if 'timed out' in str(reason).lower():
                raise SellerLeadSearchTimeoutError('Brave Search API timeout') from exc
            raise SellerLeadSearchError(f'Brave Search API network error: {reason}') from exc

        if status_code >= 400:
            _raise_for_http_status(status_code, raw_body, headers, api_key=self.api_key)

        content_type = _get_response_header(headers, 'Content-Type')
        content_encoding = _get_response_header(headers, 'Content-Encoding')
        self.last_response_info = BraveSearchResponseInfo(
            status_code=status_code,
            content_type=content_type,
            content_encoding=content_encoding,
            body_length=len(raw_body),
        )
        payload = _parse_json_response(
            raw_body,
            status_code=status_code,
            content_type=content_type,
            content_encoding=content_encoding,
            api_key=self.api_key,
        )

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
    max_new_leads: int | None = None,
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
        if max_new_leads is not None and not dry_run and stats.created >= max_new_leads:
            break
        try:
            results = search_client.search(query, count=limit)
        except SellerLeadSearchError:
            stats.errors += 1
            logger.exception('Seller lead search failed for query=%r', query)
            continue

        stats.queries_executed += 1

        if getattr(search_client, 'last_response_info', None) is not None:
            stats.api_response_info = search_client.last_response_info

        stats.results_found += len(results)

        for result in results:
            if max_new_leads is not None and not dry_run and stats.created >= max_new_leads:
                break
            profile = parse_instagram_profile_url(result['url'])
            if not profile:
                if dry_run:
                    stats.dry_run_result_details.append(
                        DryRunResultDetail(
                            title=result['title'],
                            url=result['url'],
                            username='',
                            accepted=False,
                            reason=explain_instagram_url_rejection(result['url']),
                        ),
                    )
                stats.links_rejected += 1
                continue

            username = profile['username']
            profile_url = profile['profile_url']
            stats.profiles_parsed += 1

            if username in seen_usernames:
                if dry_run:
                    stats.dry_run_result_details.append(
                        DryRunResultDetail(
                            title=result['title'],
                            url=result['url'],
                            username=username,
                            accepted=False,
                            reason='дубликат username в текущем запуске',
                        ),
                    )
                stats.duplicates_skipped += 1
                continue

            seen_usernames.add(username)

            if _seller_lead_exists(username, profile_url):
                if dry_run:
                    stats.dry_run_result_details.append(
                        DryRunResultDetail(
                            title=result['title'],
                            url=result['url'],
                            username=username,
                            accepted=False,
                            reason='уже существует в SellerLead',
                        ),
                    )
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
                if max_new_leads is not None and len(stats.dry_run_profiles) >= max_new_leads:
                    continue
                stats.dry_run_result_details.append(
                    DryRunResultDetail(
                        title=result['title'],
                        url=result['url'],
                        username=username,
                        accepted=True,
                        reason='принят как Instagram-профиль',
                    ),
                )
                stats.dry_run_profiles.append(candidate)
                continue

            display_name = result['title'].strip() or username
            lead = SellerLead.objects.create(
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
            stats.created_lead_ids.append(lead.pk)

    return stats
