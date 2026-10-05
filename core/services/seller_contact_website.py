"""Fetch a seller's own website and read public contacts from its HTML.

Google and search engines are not sources of the extracted contacts. The page
itself is. Crawling stays on the same host, respects robots.txt for extra
pages, and does not execute JavaScript, submit forms, or pretend to be a browser.
Each GET is refused unless the destination is a public HTTP(S) address.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable
from urllib import error, parse, request

from core.services.seller_discovery_identity import (
    normalize_address,
    normalize_domain,
    normalize_instagram_identity,
    normalize_seller_name,
    normalize_seller_phone,
)

logger = logging.getLogger(__name__)

USER_AGENT = 'ZPT-SellerDiscovery/1.0 (+https://zpt.kz)'
FETCH_TIMEOUT = 8.0
MAX_RESPONSE_BYTES = 512_000
MAX_PAGES = 5
MAX_REDIRECTS = 2
WHATSAPP_URI_CONFIDENCE = 98
WHATSAPP_LABEL_CONFIDENCE = 92
WHATSAPP_TEXT_CONFIDENCE = 90
PHONE_CONFIDENCE = 80
INSTAGRAM_CONFIDENCE = 85

_MULTI_PART_SUFFIXES = frozenset({'co.uk', 'com.kz', 'org.kz', 'net.kz', 'co.kz'})
_NAME_STOPWORDS = frozenset({
    'магазин',
    'автозапчасти',
    'запчасти',
    'авто',
    'shop',
    'store',
    'алматы',
    'астана',
    'шымкент',
    'казахстан',
    'ип',
    'тоо',
})
_CONTACT_LINK_MARKERS = (
    'contacts',
    'contact',
    'kontakty',
    'контакты',
    'контакты-магазина',
)
_CONTACT_FALLBACK_PATHS = (
    '/contacts',
    '/contact',
    '/kontakty',
    '/kontaktyi/',
)
_WHATSAPP_WORDS = ('whatsapp', 'вотсап', 'ватсап')
_WHATSAPP_TEXT_RE = re.compile(
    r'(?i)(?:whatsapp|вотсап|ватсап)\s*[:\-–]?\s*(\+?\d[\d\-\s()]{8,18}\d)',
)
_WHATSAPP_TRAILING_TEXT_RE = re.compile(
    r'(?i)(\+?\d[\d\-\s()]{8,18}\d)\s*(?:[:\-–—|]\s*)?(?:только\s+)?(?:whatsapp|вотсап|ватсап)',
)
_CHALLENGE_MARKERS = (
    'cf-challenge',
    'cf-browser-verification',
    'g-recaptcha',
    'hcaptcha',
    'just a moment',
    'attention required',
    'enable javascript and cookies',
)


class WebsiteFetchError(Exception):
    """Controlled fetch failure. Callers turn this into an outcome, not a retry loop."""


@dataclass(frozen=True)
class ExtractedContact:
    field_name: str
    value: str
    confidence: int
    excerpt: str
    explicit_whatsapp: bool = False


@dataclass
class WebsiteExtract:
    title: str = ''
    headings: list[str] = field(default_factory=list)
    canonical_url: str = ''
    organization_names: list[str] = field(default_factory=list)
    addresses: list[str] = field(default_factory=list)
    contacts: list[ExtractedContact] = field(default_factory=list)
    contact_links: list[str] = field(default_factory=list)
    text_sample: str = ''


@dataclass
class WebsiteCrawlResult:
    outcome: str
    final_url: str = ''
    contacts: list[ExtractedContact] = field(default_factory=list)
    identity_accepted: bool = False
    pages_fetched: int = 0
    error: str = ''


def distinctive_name_tokens(name: str) -> list[str]:
    tokens = []
    for token in normalize_seller_name(name).split():
        if len(token) < 4 or token in _NAME_STOPWORDS:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def registrable_domain(hostname: str) -> str:
    host = (hostname or '').lower().strip().rstrip('.')
    if host.startswith('www.'):
        host = host[4:]
    parts = [part for part in host.split('.') if part]
    if len(parts) >= 3 and '.'.join(parts[-2:]) in _MULTI_PART_SUFFIXES:
        return '.'.join(parts[-3:])
    if len(parts) >= 2:
        return '.'.join(parts[-2:])
    return host


def crawl_host_key(hostname: str) -> str:
    """One site: the exact host, treating www and non-www as the same host.

    shop1.example.kz and shop2.example.kz stay different. one.co.jp and
    two.co.jp stay different. This is not a public-suffix collapse.
    """
    host = (hostname or '').lower().strip().rstrip('.')
    if host.startswith('www.'):
        host = host[4:]
    return host


def same_crawl_host(seed_host: str, target_host: str) -> bool:
    """Crawl boundary: the same host, or its www / non-www pair.

    shop1.co.jp and shop2.co.jp are different sites. parts.kz and
    other.parts.kz are different sites. This is not a public-suffix check.
    """
    return bool(crawl_host_key(seed_host)) and crawl_host_key(seed_host) == crawl_host_key(target_host)


def _assert_public_destination(url: str) -> str:
    """Refuse a crawl target immediately before GET.

    This is application-level SSRF protection. DNS is resolved for this
    request only and is not cached: the next GET resolves the host again.
    """
    raw = str(url or '').strip()
    parts = parse.urlsplit(raw)
    if parts.scheme not in {'http', 'https'} or not parts.hostname:
        raise WebsiteFetchError('Сайт должен быть http или https URL.')
    if parts.username is not None or parts.password is not None:
        raise WebsiteFetchError('URL сайта не должен содержать логин или пароль.')
    if parts.scheme == 'http' and parts.port not in {None, 80}:
        raise WebsiteFetchError('Для HTTP разрешён только порт 80.')
    if parts.scheme == 'https' and parts.port not in {None, 443}:
        raise WebsiteFetchError('Для HTTPS разрешён только порт 443.')
    host = parts.hostname.lower().strip().rstrip('.')
    if host == 'localhost' or host.endswith('.localhost') or host.endswith('.local'):
        raise WebsiteFetchError('Адрес сайта не является публичным.')
    literal = _literal_ip(host)
    if literal is not None:
        if not _is_public_ip(literal):
            raise WebsiteFetchError('Адрес сайта не является публичным.')
        return raw
    try:
        answers = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        raise WebsiteFetchError('Не удалось определить адрес сайта.') from None
    if not answers:
        raise WebsiteFetchError('Не удалось определить адрес сайта.')
    for family, socktype, proto, canon, sockaddr in answers:
        del family, socktype, proto, canon
        ip_text = str(sockaddr[0]).split('%', 1)[0]
        try:
            resolved = ipaddress.ip_address(ip_text)
        except ValueError:
            raise WebsiteFetchError('Не удалось определить адрес сайта.') from None
        if not _is_public_ip(resolved):
            raise WebsiteFetchError('Адрес сайта не является публичным.')
    return raw


def _literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    text = host.split('%', 1)[0]
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        pass
    if not text or any(char not in '0123456789.x' for char in text):
        return None
    parsed = _inet_aton(text)
    if parsed is None:
        raise WebsiteFetchError('Адрес сайта не является публичным.')
    return parsed


def _inet_aton(text: str) -> ipaddress.IPv4Address | None:
    """Parse a dotted or abbreviated IPv4 literal, including 127.1."""
    parts = text.split('.')
    if not 1 <= len(parts) <= 4 or any(part == '' for part in parts):
        return None
    limits = {
        1: (0xFFFFFFFF,),
        2: (0xFF, 0xFFFFFF),
        3: (0xFF, 0xFF, 0xFFFF),
        4: (0xFF, 0xFF, 0xFF, 0xFF),
    }[len(parts)]
    values = []
    for part, limit in zip(parts, limits):
        try:
            if part.startswith('0x'):
                value = int(part, 16)
            elif len(part) > 1 and part.startswith('0'):
                if any(char not in '01234567' for char in part):
                    return None
                value = int(part, 8)
            else:
                value = int(part, 10)
        except ValueError:
            return None
        if value < 0 or value > limit:
            return None
        values.append(value)
    if len(values) == 1:
        packed = values[0]
    elif len(values) == 2:
        packed = (values[0] << 24) | values[1]
    elif len(values) == 3:
        packed = (values[0] << 24) | (values[1] << 16) | values[2]
    else:
        packed = (values[0] << 24) | (values[1] << 16) | (values[2] << 8) | values[3]
    if packed > 0xFFFFFFFF:
        return None
    return ipaddress.IPv4Address(packed)


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for a globally routable unicast address.

    Rejects loopback, private, link-local, multicast, unspecified, reserved,
    and shared address space such as carrier-grade NAT 100.64.0.0/10.
    """
    mapped = getattr(ip, 'ipv4_mapped', None)
    if mapped is not None:
        return _is_public_ip(mapped)
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or getattr(ip, 'is_site_local', False)
        or not ip.is_global
    ):
        return False
    return True


def _is_contact_page_url(url: str) -> bool:
    path = parse.unquote(parse.urlsplit(str(url or '')).path or '').casefold()
    return any(marker in path for marker in _CONTACT_LINK_MARKERS)


def parse_seller_website_html(html: str, *, page_url: str) -> WebsiteExtract:
    """Read contacts from one HTML document. The document is not returned."""
    allow_trailing_whatsapp = _is_contact_page_url(page_url)
    parser = _ContactHTMLParser(
        page_url=page_url,
        allow_trailing_whatsapp=allow_trailing_whatsapp,
    )
    parser.feed(html or '')
    parser.close()
    extract = parser.extract
    if allow_trailing_whatsapp:
        # Contact pages may split one label across adjacent spans.
        parser._consume_whatsapp_text(extract.text_sample)
    extract.contacts = _dedupe_contacts(extract.contacts)
    extract.contact_links = _unique(extract.contact_links)[:12]
    extract.text_sample = extract.text_sample[:20_000]
    return extract


def _instagram_handle_visible_in_text(text: str, handle: str) -> bool:
    """True when handle is its own Instagram token, not part of a longer one.

    A leading @ and surrounding punctuation are allowed. A following @ is an
    email, and a dot continues the token only when another username segment
    follows, so omega_auto_parts. matches and omega_auto_parts.official does not.
    """
    if not handle:
        return False
    pattern = re.compile(
        rf'(?<![A-Za-z0-9_.])@?{re.escape(handle)}(?![@A-Za-z0-9_]|\.[A-Za-z0-9_])',
        re.IGNORECASE,
    )
    return pattern.search(text or '') is not None


def website_identity_accepted(
    extract: WebsiteExtract,
    *,
    lead_name: str,
    city: str = '',
    address: str = '',
    phone: str = '',
    instagram: str = '',
    known_domain: str = '',
    page_domain: str = '',
    domain_is_prior: bool = False,
) -> bool:
    """A single brand word such as Chery is not enough to trust the page.

    known_domain counts only when it was already stored on the SellerLead.
    The URL opened in this crawl, including a Google websiteUri, is not evidence.
    """
    haystack = ' '.join([
        extract.title,
        ' '.join(extract.headings),
        ' '.join(extract.organization_names),
        extract.text_sample[:4000],
    ])
    normalized_haystack = normalize_seller_name(haystack)
    tokens = distinctive_name_tokens(lead_name)
    matched = [token for token in tokens if token in normalized_haystack]
    name_strong = len(tokens) >= 2 and len(matched) >= 2
    name_weak = bool(matched)
    phone_match = bool(phone) and any(
        item.field_name in {'phone', 'whatsapp'} and item.value == phone
        for item in extract.contacts
    )
    handle = normalize_instagram_identity(instagram)
    instagram_contact = bool(handle) and any(
        item.field_name == 'instagram' and item.value == handle
        for item in extract.contacts
    )
    # The handle can support a name match. It does not accept a page by itself.
    instagram_text = bool(handle) and _instagram_handle_visible_in_text(haystack, handle)
    instagram_match = instagram_contact or instagram_text
    city_match = bool(city) and city.casefold() in haystack.casefold()
    normalized_address = normalize_address(address)
    address_match = len(normalized_address) >= 8 and normalized_address in normalize_address(haystack)
    domain_match = (
        domain_is_prior
        and bool(known_domain)
        and known_domain == page_domain
    )
    if name_strong and (city_match or address_match or phone_match or instagram_match or domain_match):
        return True
    if name_weak and (phone_match or instagram_match or domain_match or (city_match and address_match)):
        return True
    return False


def crawl_official_website(
    start_url: str,
    *,
    lead_name: str,
    city: str = '',
    address: str = '',
    phone: str = '',
    instagram: str = '',
    known_domain: str = '',
    domain_is_prior: bool = False,
    urlopen: Callable[..., Any] | None = None,
) -> WebsiteCrawlResult:
    """GET the public site. Extra pages stay on-domain and inside robots.txt."""
    opener = urlopen or _urlopen_without_proxy
    try:
        seed = _validate_http_url(start_url)
    except WebsiteFetchError as exc:
        return WebsiteCrawlResult(outcome='error', error=str(exc))

    seed_host = parse.urlsplit(seed).hostname or ''
    robots = _load_robots(seed, opener, seed_host=seed_host)
    try:
        fetched = _fetch_page(seed, opener=opener, seed_host=seed_host, redirects_left=MAX_REDIRECTS)
    except WebsiteFetchError as exc:
        message = str(exc)
        outcome = 'error'
        if 'другой домен' in message:
            outcome = 'redirect_rejected'
        elif 'лимит размера' in message:
            outcome = 'truncated'
        elif 'challenge' in message:
            outcome = 'challenge'
        return WebsiteCrawlResult(outcome=outcome, error=str(exc))

    if not _is_html(fetched.content_type, fetched.body):
        return WebsiteCrawlResult(
            outcome='ignored',
            final_url=fetched.final_url,
            pages_fetched=1,
            error='Ответ не HTML.',
        )
    if _looks_like_challenge(fetched.body):
        return WebsiteCrawlResult(
            outcome='challenge',
            final_url=fetched.final_url,
            pages_fetched=1,
            error='Страница выглядит как captcha или anti-bot challenge. Обход не выполняется.',
        )

    combined = parse_seller_website_html(fetched.body, page_url=fetched.final_url)
    pages = 1
    seen = {fetched.final_url}
    contact_links = [
        absolute
        for link in combined.contact_links
        if (absolute := _absolute_url(fetched.final_url, link))
        and same_crawl_host(seed_host, parse.urlsplit(absolute).hostname or '')
    ]
    has_explicit_whatsapp = any(
        item.field_name == 'whatsapp' and item.explicit_whatsapp
        for item in combined.contacts
    )
    if not contact_links and not has_explicit_whatsapp:
        contact_links = [
            absolute
            for path in _CONTACT_FALLBACK_PATHS
            if (absolute := _absolute_url(fetched.final_url, path))
        ]
    for link in contact_links:
        if pages >= MAX_PAGES:
            break
        absolute = _absolute_url(fetched.final_url, link)
        if not absolute or absolute in seen:
            continue
        link_host = parse.urlsplit(absolute).hostname or ''
        if not same_crawl_host(seed_host, link_host):
            continue
        path = parse.urlsplit(absolute).path or '/'
        if robots is not None and not _robots_allows(robots, path):
            continue
        seen.add(absolute)
        try:
            extra = _fetch_page(
                absolute,
                opener=opener,
                seed_host=seed_host,
                redirects_left=MAX_REDIRECTS,
            )
        except WebsiteFetchError:
            continue
        pages += 1
        if not _is_html(extra.content_type, extra.body) or _looks_like_challenge(extra.body):
            continue
        extra_extract = parse_seller_website_html(extra.body, page_url=extra.final_url)
        combined = _merge_extracts(combined, extra_extract)

    page_domain = registrable_domain(parse.urlsplit(fetched.final_url).hostname or '')
    accepted = website_identity_accepted(
        combined,
        lead_name=lead_name,
        city=city,
        address=address,
        phone=normalize_seller_phone(phone),
        instagram=normalize_instagram_identity(instagram),
        known_domain=known_domain if domain_is_prior else '',
        page_domain=page_domain,
        domain_is_prior=domain_is_prior,
    )
    final_url = combined.canonical_url or fetched.final_url
    if not same_crawl_host(seed_host, parse.urlsplit(final_url).hostname or ''):
        final_url = fetched.final_url
    if not accepted:
        return WebsiteCrawlResult(
            outcome='ambiguous_website',
            final_url=final_url,
            pages_fetched=pages,
            identity_accepted=False,
        )
    return WebsiteCrawlResult(
        outcome='ok',
        final_url=final_url,
        contacts=list(combined.contacts),
        identity_accepted=True,
        pages_fetched=pages,
    )


def _validate_http_url(value: str) -> str:
    raw = str(value or '').strip()
    parts = parse.urlsplit(raw)
    if parts.scheme not in {'http', 'https'} or not parts.hostname:
        raise WebsiteFetchError('Сайт должен быть http или https URL.')
    return raw


def _load_robots(seed: str, opener: Callable[..., Any], *, seed_host: str) -> str | None:
    parts = parse.urlsplit(seed)
    robots_url = parse.urlunsplit((parts.scheme, parts.netloc, '/robots.txt', '', ''))
    try:
        fetched = _fetch_page(
            robots_url,
            opener=opener,
            seed_host=seed_host,
            redirects_left=1,
        )
    except WebsiteFetchError:
        return None
    if _looks_like_challenge(fetched.body):
        return None
    return fetched.body


def _fetch_page(url: str, *, opener: Callable[..., Any], seed_host: str, redirects_left: int):
    current = url
    remaining = redirects_left
    while True:
        current = _assert_public_destination(current)
        current_host = parse.urlsplit(current).hostname or ''
        if not same_crawl_host(seed_host, current_host):
            raise WebsiteFetchError('Редирект уводит на другой домен.')
        http_request = request.Request(
            current,
            headers={
                'Accept': 'text/html,application/xhtml+xml,text/plain',
                'User-Agent': USER_AGENT,
            },
            method='GET',
        )
        logger.info('Seller website GET host=%s path=%s', current_host, parse.urlsplit(current).path)
        try:
            with opener(http_request, timeout=FETCH_TIMEOUT) as response:
                status = getattr(response, 'status', 200)
                headers = getattr(response, 'headers', {})
                body = _read_limited(response)
        except error.HTTPError as exc:
            raise WebsiteFetchError(f'Сайт ответил HTTP {exc.code}') from None
        except error.URLError as exc:
            reason = str(getattr(exc, 'reason', exc)).lower()
            if 'timed out' in reason:
                raise WebsiteFetchError('Таймаут при чтении сайта') from None
            raise WebsiteFetchError('Сетевая ошибка при чтении сайта') from None
        except TimeoutError:
            raise WebsiteFetchError('Таймаут при чтении сайта') from None
        except OSError:
            # Low-level socket/TLS failures (for example ConnectionResetError)
            # are ordinary remote-site failures and must never abort a batch.
            raise WebsiteFetchError('Сетевая ошибка при чтении сайта') from None
        except WebsiteFetchError:
            raise

        location = _header(headers, 'Location')
        if status in {301, 302, 303, 307, 308} and location:
            if remaining <= 0:
                raise WebsiteFetchError('Слишком много редиректов.')
            target = _absolute_url(current, location)
            if not target:
                raise WebsiteFetchError('Редирект уводит на другой домен.')
            remaining -= 1
            current = target
            continue
        content_type = _header(headers, 'Content-Type')
        return _Fetched(final_url=current, status=status, content_type=content_type, body=body)


@dataclass
class _Fetched:
    final_url: str
    status: int
    content_type: str
    body: str


def _read_limited(response: Any) -> str:
    chunks: list[bytes] = []
    total = 0
    while total <= MAX_RESPONSE_BYTES:
        piece = response.read(8192)
        if not piece:
            break
        if isinstance(piece, str):
            piece = piece.encode('utf-8', errors='replace')
        total += len(piece)
        if total > MAX_RESPONSE_BYTES:
            raise WebsiteFetchError('Ответ сайта превышает лимит размера и отклонён.')
        chunks.append(piece)
    return b''.join(chunks).decode('utf-8', errors='replace')


def _is_html(content_type: str, body: str) -> bool:
    lowered = (content_type or '').lower()
    if 'html' in lowered or 'xml' in lowered:
        return True
    if lowered and 'text/plain' not in lowered and 'text/' in lowered:
        return False
    if any(marker in lowered for marker in ('pdf', 'image/', 'octet-stream', 'zip', 'json')):
        return False
    sample = (body or '')[:400].lstrip().lower()
    return sample.startswith('<!doctype html') or sample.startswith('<html') or '<html' in sample[:200]


def _looks_like_challenge(body: str) -> bool:
    sample = (body or '')[:8000].lower()
    return any(marker in sample for marker in _CHALLENGE_MARKERS)


def _robots_allows(robots_text: str, path: str) -> bool:
    groups = _robot_groups(robots_text)
    selected = groups.get('zpt-sellerdiscovery') or groups.get('*')
    if not selected:
        return True
    target = path or '/'
    decision = True
    longest = -1
    for rule, allowed in selected:
        if target.startswith(rule) and len(rule) > longest:
            longest = len(rule)
            decision = allowed
    return decision


def _robot_groups(robots_text: str) -> dict[str, list[tuple[str, bool]]]:
    groups: dict[str, list[tuple[str, bool]]] = {}
    current: list[str] = []
    for raw_line in (robots_text or '').splitlines():
        line = raw_line.split('#', 1)[0].strip()
        if not line or ':' not in line:
            continue
        key, value = line.split(':', 1)
        key = key.strip().lower()
        value = value.strip()
        if key == 'user-agent':
            current = [value.lower()]
            for agent in current:
                groups.setdefault(agent, [])
            continue
        if key == 'disallow' and not value:
            continue
        if key in {'allow', 'disallow'} and current:
            rule = value or '/'
            if not rule.startswith('/'):
                rule = '/' + rule
            for agent in current:
                groups.setdefault(agent, []).append((rule, key == 'allow'))
    return groups


def _header(headers: Any, name: str) -> str:
    if headers is None:
        return ''
    if hasattr(headers, 'get'):
        value = headers.get(name)
        if value:
            return str(value)
        value = headers.get(name.lower())
        return str(value or '')
    return ''


def _absolute_url(base: str, href: str) -> str:
    raw = str(href or '').strip()
    if not raw or raw.startswith(('#', 'mailto:', 'tel:', 'javascript:', 'whatsapp:')):
        return ''
    return parse.urljoin(base, raw)


def _merge_extracts(left: WebsiteExtract, right: WebsiteExtract) -> WebsiteExtract:
    left.headings.extend(right.headings)
    left.organization_names.extend(right.organization_names)
    left.addresses.extend(right.addresses)
    left.contacts.extend(right.contacts)
    left.contacts = _dedupe_contacts(left.contacts)
    left.contact_links.extend(right.contact_links)
    left.text_sample = (left.text_sample + ' ' + right.text_sample)[:20_000]
    if not left.canonical_url:
        left.canonical_url = right.canonical_url
    return left


def _dedupe_contacts(contacts: list[ExtractedContact]) -> list[ExtractedContact]:
    best: dict[tuple[str, str], ExtractedContact] = {}
    for item in contacts:
        key = (item.field_name, item.value)
        current = best.get(key)
        if current is None or item.confidence > current.confidence:
            best[key] = item
    return list(best.values())


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


class _ContactHTMLParser(HTMLParser):
    def __init__(self, *, page_url: str, allow_trailing_whatsapp: bool = False):
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.allow_trailing_whatsapp = allow_trailing_whatsapp
        self.extract = WebsiteExtract()
        self._ignore_depth = 0
        self._capture_title = False
        self._capture_h1 = False
        self._capture_json = False
        self._json_chunks: list[str] = []
        self._anchor_href = ''
        self._anchor_text: list[str] = []
        self._text_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        attributes = {key.lower(): (value or '') for key, value in attrs}
        kind = tag.lower()
        if kind in {'script', 'style', 'noscript'} and attributes.get('type', '').lower() != 'application/ld+json':
            self._ignore_depth += 1
        if kind == 'script' and 'ld+json' in attributes.get('type', '').lower():
            self._capture_json = True
            self._json_chunks = []
        if kind == 'title':
            self._capture_title = True
        if kind == 'h1':
            self._capture_h1 = True
        if kind == 'a':
            self._anchor_href = attributes.get('href', '')
            self._anchor_text = []
        if kind == 'link' and 'canonical' in attributes.get('rel', '').lower():
            canonical = _absolute_url(self.page_url, attributes.get('href', ''))
            if canonical:
                self.extract.canonical_url = canonical

    def handle_endtag(self, tag: str):
        kind = tag.lower()
        if kind in {'script', 'style', 'noscript'} and self._ignore_depth and not self._capture_json:
            self._ignore_depth = max(0, self._ignore_depth - 1)
        if kind == 'script' and self._capture_json:
            self._capture_json = False
            self._consume_json(''.join(self._json_chunks))
            self._json_chunks = []
        if kind == 'title':
            self._capture_title = False
        if kind == 'h1':
            self._capture_h1 = False
        if kind == 'a' and self._anchor_href:
            self._consume_anchor(self._anchor_href, ''.join(self._anchor_text))
            self._anchor_href = ''
            self._anchor_text = []

    def handle_data(self, data: str):
        if self._capture_json:
            self._json_chunks.append(data)
            return
        if self._ignore_depth:
            return
        text = ' '.join(str(data or '').split())
        if not text:
            return
        if self._capture_title:
            self.extract.title = ' '.join((self.extract.title, text)).strip()
        if self._capture_h1:
            self.extract.headings.append(text)
        if self._anchor_href:
            self._anchor_text.append(text)
        if len(self.extract.text_sample) < 20_000:
            self._text_chunks.append(text)
            self.extract.text_sample = ' '.join(self._text_chunks)[:20_000]
        self._consume_whatsapp_text(text)

    def _consume_anchor(self, href: str, text: str):
        absolute = _absolute_url(self.page_url, href)
        label = ' '.join(text.split())
        lowered_href = (href or '').strip().lower()
        lowered_label = label.casefold()
        if _is_contact_link(lowered_href, lowered_label) and absolute:
            self.extract.contact_links.append(absolute)
        phone = _phone_from_whatsapp_url(href)
        if phone:
            self.extract.contacts.append(ExtractedContact(
                field_name='whatsapp',
                value=phone,
                confidence=WHATSAPP_URI_CONFIDENCE,
                excerpt=label or href,
                explicit_whatsapp=True,
            ))
            return
        if lowered_href.startswith('tel:'):
            number = normalize_seller_phone(parse.unquote(href.split(':', 1)[1]))
            if not number:
                return
            labelled = any(word in lowered_label for word in _WHATSAPP_WORDS)
            self.extract.contacts.append(ExtractedContact(
                field_name='whatsapp' if labelled else 'phone',
                value=number,
                confidence=WHATSAPP_LABEL_CONFIDENCE if labelled else PHONE_CONFIDENCE,
                excerpt=label or href,
                explicit_whatsapp=labelled,
            ))
            return
        if not _is_instagram_href(absolute or href):
            return
        username = normalize_instagram_identity(absolute or href)
        if username:
            self.extract.contacts.append(ExtractedContact(
                field_name='instagram',
                value=username,
                confidence=INSTAGRAM_CONFIDENCE,
                excerpt=label or href,
            ))

    def _consume_whatsapp_text(self, text: str):
        patterns = [_WHATSAPP_TEXT_RE]
        if self.allow_trailing_whatsapp:
            patterns.append(_WHATSAPP_TRAILING_TEXT_RE)
        for pattern in patterns:
            for match in pattern.finditer(text):
                number = normalize_seller_phone(match.group(1))
                if not number:
                    continue
                self.extract.contacts.append(ExtractedContact(
                    field_name='whatsapp',
                    value=number,
                    confidence=WHATSAPP_TEXT_CONFIDENCE,
                    excerpt=match.group(0)[:180],
                    explicit_whatsapp=True,
                ))

    def _consume_json(self, raw: str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        self._walk_json(payload)

    def _walk_json(self, node: Any):
        if isinstance(node, list):
            for item in node:
                self._walk_json(item)
            return
        if not isinstance(node, dict):
            return
        name = node.get('name')
        if isinstance(name, str) and name.strip():
            self.extract.organization_names.append(' '.join(name.split())[:255])
        address = node.get('address')
        if isinstance(address, str):
            self.extract.addresses.append(address[:500])
        elif isinstance(address, dict):
            parts = [
                str(address.get(key) or '')
                for key in ('streetAddress', 'addressLocality', 'addressRegion')
            ]
            joined = ' '.join(part for part in parts if part).strip()
            if joined:
                self.extract.addresses.append(joined[:500])
        contact_type = str(node.get('contactType') or '')
        telephone = node.get('telephone')
        phones = telephone if isinstance(telephone, list) else [telephone]
        explicit = 'whatsapp' in contact_type.casefold()
        for item in phones:
            number = normalize_seller_phone(str(item or ''))
            if not number:
                continue
            self.extract.contacts.append(ExtractedContact(
                field_name='whatsapp' if explicit else 'phone',
                value=number,
                confidence=WHATSAPP_TEXT_CONFIDENCE if explicit else PHONE_CONFIDENCE,
                excerpt='json-ld',
                explicit_whatsapp=explicit,
            ))
        for value in node.values():
            if isinstance(value, (dict, list)):
                self._walk_json(value)


def _is_instagram_href(href: str) -> bool:
    """True only for an http(s) Instagram profile URL, not a generic href."""
    raw = str(href or '').strip()
    if not raw:
        return False
    lowered = raw.lower()
    if lowered.startswith(('javascript:', 'mailto:', 'tel:', 'sms:', 'data:', '#')):
        return False
    if '://' not in raw:
        return False
    parts = parse.urlsplit(raw)
    if parts.scheme not in {'http', 'https'}:
        return False
    host = (parts.hostname or '').lower().strip().rstrip('.')
    return host in {'instagram.com', 'www.instagram.com', 'm.instagram.com'}


def _is_contact_link(href: str, label: str) -> bool:
    blob = f'{href} {label}'.casefold()
    return any(marker in blob for marker in _CONTACT_LINK_MARKERS)


def _phone_from_whatsapp_url(href: str) -> str:
    raw = str(href or '').strip()
    lowered = raw.lower()
    if lowered.startswith('https://wa.me/') or lowered.startswith('http://wa.me/') or lowered.startswith('//wa.me/'):
        path = parse.urlsplit(raw if '://' in raw else 'https:' + raw).path.strip('/')
        return normalize_seller_phone(path.split('/')[0])
    if 'api.whatsapp.com' in lowered or lowered.startswith('whatsapp:'):
        query = parse.parse_qs(parse.urlsplit(raw if '://' in raw else 'https://' + raw.lstrip('/')).query)
        phone = (query.get('phone') or [''])[0]
        return normalize_seller_phone(phone)
    return ''


class _NoFollowRedirect(request.HTTPRedirectHandler):
    """Leave 3xx responses to the crawler so a foreign domain is never fetched."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    def http_error_301(self, req, fp, code, msg, headers):
        return fp

    http_error_302 = http_error_303 = http_error_307 = http_error_308 = http_error_301


def _urlopen_without_proxy(http_request: request.Request, timeout: float):
    opener = request.build_opener(
        request.ProxyHandler({}),
        request.HTTPHandler(),
        request.HTTPSHandler(),
        request.HTTPErrorProcessor(),
        _NoFollowRedirect(),
    )
    return opener.open(http_request, timeout=timeout)
