"""
Очистка пользовательского текста заявки перед публикацией в Instagram.
"""

from __future__ import annotations

import re

DEFAULT_MAX_DESCRIPTION_LENGTH = 200

_PHONE_PATTERN = re.compile(
    r'(?:'
    r'\+?\d[\d\s().\-]{8,}\d'
    r'|'
    r'\b(?:7|8)\d{10}\b'
    r'|'
    r'\b\d{3}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b'
    r')',
    flags=re.IGNORECASE,
)

_EMAIL_PATTERN = re.compile(
    r'\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b',
    flags=re.IGNORECASE,
)

_URL_PATTERN = re.compile(
    r'(?:https?://|www\.)\S+|'
    r'\b[a-z0-9][a-z0-9\-]*\.(?:kz|ru|com|net|org|io|me|app|shop|store)\S*',
    flags=re.IGNORECASE,
)

_VIN_PATTERN = re.compile(r'\b[A-HJ-NPR-Z0-9]{17}\b', flags=re.IGNORECASE)


def sanitize_description(
    value: str | None,
    *,
    max_length: int = DEFAULT_MAX_DESCRIPTION_LENGTH,
) -> str:
    """
    Удаляет телефоны, email, ссылки, VIN-подобные значения и лишние пробелы.
    """
    text = ' '.join(str(value or '').split())
    if not text:
        return ''

    text = _PHONE_PATTERN.sub(' ', text)
    text = _EMAIL_PATTERN.sub(' ', text)
    text = _URL_PATTERN.sub(' ', text)
    text = _VIN_PATTERN.sub(' ', text)
    text = ' '.join(text.split())

    if len(text) > max_length:
        text = text[: max_length - 1].rstrip() + '…'

    return text
