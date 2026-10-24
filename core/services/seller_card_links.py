"""URL checks for seller cards.

Card links are accepted only when the saved URL belongs to the declared
map source. A search by organization name is never built here.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse, urlunparse

from core.phone_utils import normalize_phone_for_whatsapp, is_excluded_city_whatsapp


SAFE_SCHEMES = {'http', 'https'}

SOCIAL_DOMAINS = {
    'instagram': ('instagram.com',),
    'telegram': ('t.me', 'telegram.me', 'telegram.org'),
    'tiktok': ('tiktok.com',),
    'vk': ('vk.com', 'vk.ru'),
    'youtube': ('youtube.com', 'youtu.be'),
    'whatsapp': ('wa.me', 'whatsapp.com'),
}

CARD_RULES = {
    'two_gis': {
        'hosts': ('2gis.kz', '2gis.ru', '2gis.com', '2gis.kg', '2gis.uz'),
        'markers': ('/firm/', '/geo/'),
    },
    'yandex': {
        'hosts': ('yandex.ru', 'yandex.kz', 'yandex.com', 'yandex.uz'),
        'markers': ('/maps/org/', '/org/'),
    },
    'google': {
        'hosts': ('google.com', 'google.kz', 'maps.google.com', 'goo.gl'),
        'markers': ('/maps',),
    },
}

TRACKING_PARAMS = {
    'utm_source',
    'utm_medium',
    'utm_campaign',
    'utm_term',
    'utm_content',
    'fbclid',
    'si',
    'is_from_webapp',
    'sender_device',
    'igshid',
}


def host_of(url: str) -> str:
    host = (urlparse(url).hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    return host


def host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == suffix or host.endswith('.' + suffix) for suffix in suffixes)


def clean_http_url(raw: object) -> tuple[str, str]:
    """Return (url, error). Error is empty when the URL is usable."""
    text = str(raw or '').strip()
    if not text or text.lower() in {'none', 'null', '-'}:
        return '', ''
    if '://' in text:
        scheme = text.split('://', 1)[0].lower()
        if scheme not in SAFE_SCHEMES:
            return '', 'небезопасная схема ссылки'
    elif ':' in text.split('/', 1)[0]:
        return '', 'небезопасная схема ссылки'
    else:
        text = 'https://' + text.lstrip('/')
    parsed = urlparse(text)
    try:
        port = parsed.port
    except ValueError:
        return '', 'некорректный URL'
    if parsed.scheme not in SAFE_SCHEMES or not parsed.hostname:
        return '', 'некорректный URL'
    if parsed.username or parsed.password:
        return '', 'некорректный URL'
    query = parse_qs(parsed.query, keep_blank_values=False)
    filtered = [
        (key, values[0])
        for key, values in query.items()
        if key.lower() not in TRACKING_PARAMS and values
    ]
    query_text = '&'.join(f'{key}={value}' for key, value in filtered)
    path = parsed.path or ''
    path = path.rstrip('.,;')
    if path != '/' and path.endswith('/'):
        path = path[:-1]
    host = host_of(text)
    netloc = host
    if port and port not in (80, 443):
        netloc = f'{host}:{parsed.port}'
    return urlunparse(('https', netloc, path, '', query_text, '')), ''


def classify_social(url: str) -> str:
    host = host_of(url)
    for kind, suffixes in SOCIAL_DOMAINS.items():
        if host_matches(host, suffixes):
            return kind
    return ''


def is_map_host(url: str) -> bool:
    host = host_of(url)
    return any(marker in host for marker in ('2gis', 'yandex', 'google', 'goo.gl'))


def validate_card_url(source: str, raw: object) -> tuple[str, str]:
    cleaned, error = clean_http_url(raw)
    if error or not cleaned:
        return '', error or 'пустая ссылка на карточку'
    rule = CARD_RULES.get(source)
    if rule is None:
        return '', 'неизвестный источник карточки'
    host = host_of(cleaned)
    path = urlparse(cleaned).path.lower()
    if not host_matches(host, rule['hosts']):
        return '', 'домен ссылки не соответствует источнику'
    if not any(marker in path for marker in rule['markers']):
        return '', 'ссылка не ведёт на карточку организации'
    return cleaned, ''


def whatsapp_digits(url: str) -> str:
    parsed = urlparse(url)
    host = host_of(url)
    if host == 'wa.me' or host.endswith('.wa.me'):
        segment = parsed.path.strip('/').split('/', 1)[0]
        return ''.join(ch for ch in segment if ch.isdigit())
    query = parse_qs(parsed.query)
    raw = (query.get('phone') or [''])[0]
    return ''.join(ch for ch in raw if ch.isdigit())


def assess_whatsapp_link(raw: object, contact_phones: set[str]) -> tuple[str, str, str]:
    """Return (url, digits, problem).

    problem is empty for a link that can be stored. A phone in the contacts
    column is not treated as a working WhatsApp number by itself.
    """
    cleaned, error = clean_http_url(raw)
    if error:
        return '', '', error
    if not cleaned:
        return '', '', ''
    if classify_social(cleaned) != 'whatsapp':
        return '', '', 'домен не относится к WhatsApp'
    digits_raw = whatsapp_digits(cleaned)
    if not digits_raw:
        return '', '', 'в ссылке WhatsApp нет номера'
    normalized = normalize_phone_for_whatsapp(digits_raw)
    if not normalized or is_excluded_city_whatsapp(normalized):
        return '', '', 'подозрительный номер WhatsApp'
    for contact in contact_phones:
        if contact and contact != normalized and contact in normalized:
            return '', '', 'подозрительный номер WhatsApp: лишние цифры'
    if contact_phones and normalized not in contact_phones:
        return '', '', 'номер WhatsApp не совпадает с телефонами карточки'
    stored = f'https://wa.me/{normalized}'
    return stored, normalized, ''


def normalize_social_url(kind: str, raw: object, contact_phones: set[str] | None = None) -> tuple[str, str]:
    if kind == 'whatsapp':
        url, _digits, problem = assess_whatsapp_link(raw, contact_phones or set())
        return url, problem
    cleaned, error = clean_http_url(raw)
    if error or not cleaned:
        return '', error or 'пустая ссылка'
    detected = classify_social(cleaned)
    if detected != kind:
        return '', 'домен ссылки не соответствует площадке'
    return cleaned, ''
