from __future__ import annotations

import json
import os
import re
from typing import Any

_REDACTED = '[REDACTED]'
_BEARER_PATTERN = re.compile(r'Bearer\s+\S+', re.IGNORECASE)
_ACCESS_TOKEN_QUERY_PATTERN = re.compile(
    r'access_token=[^\s&"\']+',
    re.IGNORECASE,
)
_META_TOKEN_LIKE_PATTERN = re.compile(r'\bEAA[A-Za-z0-9]{20,}\b')


def _configured_access_token() -> str:
    return (os.getenv('WHATSAPP_ACCESS_TOKEN') or '').strip()


def redact_whatsapp_sensitive_data(value: Any) -> Any:
    if value is None:
        return value
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, dict):
        return {
            str(key): redact_whatsapp_sensitive_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_whatsapp_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_whatsapp_sensitive_data(item) for item in value)
    try:
        return _redact_string(str(value))
    except Exception:
        return value


def _redact_string(text: str) -> str:
    if not text:
        return text

    token = _configured_access_token()
    result = text
    if token and token in result:
        result = result.replace(token, _REDACTED)

    result = _BEARER_PATTERN.sub(f'Bearer {_REDACTED}', result)
    result = _ACCESS_TOKEN_QUERY_PATTERN.sub(f'access_token={_REDACTED}', result)
    result = _META_TOKEN_LIKE_PATTERN.sub(_REDACTED, result)
    return result


def sanitize_whatsapp_error_payload(raw: Any) -> Any:
    if raw is None:
        return raw
    if isinstance(raw, (dict, list)):
        return redact_whatsapp_sensitive_data(raw)
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return ''
        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            return redact_whatsapp_sensitive_data(raw)
        return redact_whatsapp_sensitive_data(parsed)
    return redact_whatsapp_sensitive_data(str(raw))


def sanitize_persisted_whatsapp_error_message(value: Any, *, max_length: int = 2000) -> str:
    sanitized = redact_whatsapp_sensitive_data(value)
    if isinstance(sanitized, (dict, list)):
        try:
            text = json.dumps(sanitized, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(sanitized)
    else:
        text = str(sanitized or '')
    return text[:max_length]
