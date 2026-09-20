"""Validated public Kaspi product URLs. Never derived from master_sku."""

from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

ALLOWED_HOSTS = frozenset({'kaspi.kz', 'www.kaspi.kz'})
PRODUCT_PATH_PREFIX = '/shop/p/'


def validate_kaspi_public_url(value: str) -> None:
    text = str(value or '').strip()
    if not text:
        return
    parsed = urlsplit(text)
    if parsed.scheme != 'https':
        raise ValidationError(
            'Публичная ссылка Kaspi должна быть HTTPS.',
            code='kaspi_url_https',
        )
    host = (parsed.hostname or '').lower()
    if host not in ALLOWED_HOSTS:
        raise ValidationError(
            'Разрешён только адрес kaspi.kz.',
            code='kaspi_url_host',
        )
    if parsed.port not in (None, 443):
        raise ValidationError(
            'Разрешён только стандартный HTTPS-порт Kaspi.',
            code='kaspi_url_port',
        )
    if parsed.username or parsed.password:
        raise ValidationError(
            'Ссылка Kaspi не должна содержать учётные данные.',
            code='kaspi_url_userinfo',
        )
    path = parsed.path or ''
    if '/shop/api/' in path:
        raise ValidationError(
            'API-ссылки Kaspi не допускаются.',
            code='kaspi_url_api',
        )
    if not path.startswith(PRODUCT_PATH_PREFIX):
        raise ValidationError(
            'Ссылка должна вести на публичную карточку /shop/p/.',
            code='kaspi_url_path',
        )
    slug = path[len(PRODUCT_PATH_PREFIX):].strip('/')
    if not slug:
        raise ValidationError(
            'Укажите точную карточку товара Kaspi.',
            code='kaspi_url_empty_product',
        )


def display_kaspi_public_url(value: str) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    try:
        validate_kaspi_public_url(text)
    except ValidationError:
        return ''
    return text


MIN_PUBLIC_PRODUCT_ID_DIGITS = 6


def kaspi_product_id_from_public_url(value: str) -> str | None:
    """Return the trailing numeric Kaspi card id from a stored /shop/p/ URL.

    Uses only an already-bound public_url. Does not search Kaspi, does not
    guess from article/merchant SKU, and does not merge analog cards.
    """

    text = display_kaspi_public_url(value)
    if not text:
        return None
    path = (urlsplit(text).path or '').rstrip('/')
    if not path.startswith(PRODUCT_PATH_PREFIX):
        return None
    slug = path[len(PRODUCT_PATH_PREFIX):]
    token = slug.rsplit('-', 1)[-1].strip()
    if token.isdigit() and len(token) >= MIN_PUBLIC_PRODUCT_ID_DIGITS:
        return token
    return None
