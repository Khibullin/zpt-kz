"""Signed remote-image tokens and SSRF-safe download into MEDIA."""

from __future__ import annotations

import ipaddress
import logging
import socket
from io import BytesIO
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urljoin, urlparse

from django.core.files.base import ContentFile
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

PHOTO_TOKEN_SALT = 'catalog.product-remote-image'
PHOTO_TOKEN_MAX_AGE = 60 * 60 * 2
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 3
DOWNLOAD_TIMEOUT = 10
ALLOWED_SCHEMES = frozenset({'http', 'https'})
ALLOWED_PIL_FORMATS = {
    'JPEG': '.jpg',
    'PNG': '.png',
    'WEBP': '.webp',
}
BLOCKED_HOSTS = frozenset({
    'localhost',
    'localhost.localdomain',
    'ip6-localhost',
    'ip6-loopback',
    'metadata.google.internal',
})


class RemoteImageError(Exception):
    """User-facing remote image failure."""


class RemoteImageBlockedError(RemoteImageError):
    """SSRF / private-network rejection."""


class _NoRedirectHandler(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _signer() -> TimestampSigner:
    return TimestampSigner(salt=PHOTO_TOKEN_SALT)


def sign_remote_image_token(
    *,
    image_url: str,
    thumbnail_url: str = '',
    source_url: str = '',
    title: str = '',
) -> str:
    return _signer().sign_object({
        'u': image_url,
        'th': thumbnail_url or '',
        's': source_url or '',
        't': title or '',
    })


def read_remote_image_token(token: str, *, max_age: int = PHOTO_TOKEN_MAX_AGE) -> dict[str, str]:
    raw = (token or '').strip()
    if not raw:
        raise RemoteImageError('Некорректный выбор фото.')
    try:
        data = _signer().unsign_object(raw, max_age=max_age)
    except SignatureExpired as exc:
        raise RemoteImageError(
            'Срок выбора фото истёк. Найдите фото ещё раз.'
        ) from exc
    except (BadSignature, ValueError, TypeError) as exc:
        raise RemoteImageError('Некорректный выбор фото.') from exc
    if not isinstance(data, dict):
        raise RemoteImageError('Некорректный выбор фото.')
    image_url = str(data.get('u') or '').strip()
    if not image_url:
        raise RemoteImageError('Некорректный выбор фото.')
    assert_public_http_url(image_url, resolve_dns=False)
    return {
        'image_url': image_url,
        'thumbnail_url': str(data.get('th') or '').strip(),
        'source_url': str(data.get('s') or '').strip(),
        'title': str(data.get('t') or '').strip(),
    }


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or (getattr(ip, 'is_site_local', False))
    )


def _hostname_is_blocked(hostname: str) -> bool:
    host = (hostname or '').strip().lower().rstrip('.')
    if not host:
        return True
    if host in BLOCKED_HOSTS:
        return True
    if host.endswith('.localhost') or host.endswith('.local'):
        return True
    return False


def _parse_ip(hostname: str):
    host = (hostname or '').strip()
    if host.startswith('[') and host.endswith(']'):
        host = host[1:-1]
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def resolve_host_ips(
    hostname: str,
    *,
    resolver: Callable[[str], list] | None = None,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    host = (hostname or '').strip()
    literal = _parse_ip(host)
    if literal is not None:
        return [literal]

    lookup = resolver or _default_resolver
    try:
        infos = lookup(host)
    except OSError as exc:
        raise RemoteImageBlockedError(
            'Нельзя загрузить изображение с этого адреса.'
        ) from exc

    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4] if isinstance(info, tuple) and len(info) > 4 else None
        raw_ip = sockaddr[0] if sockaddr else None
        if not raw_ip:
            continue
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        key = str(ip)
        if key in seen:
            continue
        seen.add(key)
        ips.append(ip)
    if not ips:
        raise RemoteImageBlockedError('Нельзя загрузить изображение с этого адреса.')
    return ips


def _default_resolver(hostname: str) -> list:
    return socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)


def assert_public_http_url(
    url: str,
    *,
    resolver: Callable[[str], list] | None = None,
    resolve_dns: bool = True,
) -> str:
    raw = (url or '').strip()
    if not raw:
        raise RemoteImageBlockedError('Нельзя загрузить изображение с этого адреса.')
    parsed = urlparse(raw)
    scheme = (parsed.scheme or '').lower()
    if scheme not in ALLOWED_SCHEMES:
        raise RemoteImageBlockedError('Нельзя загрузить изображение с этого адреса.')
    hostname = parsed.hostname or ''
    if not hostname or _hostname_is_blocked(hostname):
        raise RemoteImageBlockedError('Нельзя загрузить изображение с этого адреса.')
    if parsed.username is not None or parsed.password is not None:
        raise RemoteImageBlockedError('Нельзя загрузить изображение с этого адреса.')
    literal = _parse_ip(hostname)
    if literal is not None:
        if _ip_is_blocked(literal):
            raise RemoteImageBlockedError('Нельзя загрузить изображение с этого адреса.')
        return raw
    if resolve_dns:
        for ip in resolve_host_ips(hostname, resolver=resolver):
            if _ip_is_blocked(ip):
                raise RemoteImageBlockedError('Нельзя загрузить изображение с этого адреса.')
    return raw


def _header_value(headers: Any, name: str) -> str:
    if headers is None:
        return ''
    value = headers.get(name) if hasattr(headers, 'get') else ''
    if not value and hasattr(headers, 'get_all'):
        values = headers.get_all(name) or headers.get_all(name.lower()) or []
        value = values[0] if values else ''
    if not value and isinstance(headers, dict):
        value = headers.get(name) or headers.get(name.lower()) or ''
    return str(value or '').strip()


def _read_limited(response, *, limit: int = MAX_IMAGE_BYTES) -> bytes:
    content_length = _header_value(getattr(response, 'headers', None), 'Content-Length')
    if content_length.isdigit() and int(content_length) > limit:
        raise RemoteImageError('Файл больше 5 МБ. Загрузите своё фото меньшего размера.')

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise RemoteImageError('Файл больше 5 МБ. Загрузите своё фото меньшего размера.')
        chunks.append(chunk)
    return b''.join(chunks)


def _inspect_image(data: bytes) -> str:
    if not data:
        raise RemoteImageError('Не удалось прочитать изображение. Загрузите своё фото.')
    try:
        with Image.open(BytesIO(data)) as verified:
            verified.verify()
        with Image.open(BytesIO(data)) as image:
            fmt = (image.format or '').upper()
            if fmt not in ALLOWED_PIL_FORMATS:
                raise RemoteImageError('Допустимы только JPEG, PNG и WebP. Загрузите своё фото.')
            image.load()
            return fmt
    except RemoteImageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise RemoteImageError(
            'Файл не является корректным изображением. Загрузите своё фото.'
        ) from exc


def _open_without_auto_redirect(http_request: urllib_request.Request, timeout: float):
    opener = urllib_request.build_opener(
        urllib_request.ProxyHandler({}),
        _NoRedirectHandler,
    )
    return opener.open(http_request, timeout=timeout)


def download_public_image(
    url: str,
    *,
    urlopen: Callable[..., Any] | None = None,
    resolver: Callable[[str], list] | None = None,
    timeout: float = DOWNLOAD_TIMEOUT,
) -> tuple[bytes, str]:
    """Download a public image. Returns (bytes, pil_format)."""
    current = assert_public_http_url(url, resolver=resolver)
    open_url = urlopen or _open_without_auto_redirect

    for redirect_count in range(MAX_REDIRECTS + 1):
        assert_public_http_url(current, resolver=resolver)
        http_request = urllib_request.Request(
            current,
            headers={'User-Agent': 'ZPT.KZ product image import'},
            method='GET',
        )
        try:
            with open_url(http_request, timeout=timeout) as response:
                status = int(getattr(response, 'status', 200) or 200)
                if status in {301, 302, 303, 307, 308}:
                    location = _header_value(response.headers, 'Location')
                    if not location:
                        raise RemoteImageError('Не удалось загрузить фото. Загрузите своё фото.')
                    if redirect_count >= MAX_REDIRECTS:
                        raise RemoteImageError('Не удалось загрузить фото. Загрузите своё фото.')
                    current = urljoin(current, location)
                    continue
                if status >= 400:
                    raise RemoteImageError('Не удалось загрузить фото. Загрузите своё фото.')
                payload = _read_limited(response)
                fmt = _inspect_image(payload)
                return payload, fmt
        except RemoteImageError:
            raise
        except urllib_error.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308}:
                location = _header_value(exc.headers, 'Location')
                if not location:
                    raise RemoteImageError('Не удалось загрузить фото. Загрузите своё фото.') from exc
                if redirect_count >= MAX_REDIRECTS:
                    raise RemoteImageError('Не удалось загрузить фото. Загрузите своё фото.') from exc
                current = urljoin(current, location)
                continue
            raise RemoteImageError('Не удалось загрузить фото. Загрузите своё фото.') from exc
        except urllib_error.URLError as exc:
            reason = str(getattr(exc, 'reason', exc)).lower()
            if 'timed out' in reason:
                raise RemoteImageError(
                    'Превышено время загрузки фото. Загрузите своё фото.'
                ) from exc
            raise RemoteImageError('Не удалось загрузить фото. Загрузите своё фото.') from exc
        except TimeoutError as exc:
            raise RemoteImageError(
                'Превышено время загрузки фото. Загрузите своё фото.'
            ) from exc

    raise RemoteImageError('Не удалось загрузить фото. Загрузите своё фото.')


def fetch_signed_remote_image(
    token: str,
    *,
    filename_stem: str = 'product',
    urlopen: Callable[..., Any] | None = None,
    resolver: Callable[[str], list] | None = None,
) -> ContentFile:
    payload = read_remote_image_token(token)
    data, fmt = download_public_image(
        payload['image_url'],
        urlopen=urlopen,
        resolver=resolver,
    )
    ext = ALLOWED_PIL_FORMATS[fmt]
    stem = (filename_stem or 'product').strip() or 'product'
    safe_stem = ''.join(ch if ch.isalnum() or ch in '-_' else '-' for ch in stem)[:60]
    content = ContentFile(data, name=f'{safe_stem or "product"}{ext}')
    return content
