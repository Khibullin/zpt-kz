"""Session snapshot of campaign UTM params for later Order attribution."""

from .constants import (
    SESSION_UTM_KEY,
    UTM_CAMPAIGN_MAX_LENGTH,
    UTM_MEDIUM_MAX_LENGTH,
    UTM_SOURCE_MAX_LENGTH,
)

_UTM_MAX_LENGTHS = {
    'utm_source': UTM_SOURCE_MAX_LENGTH,
    'utm_medium': UTM_MEDIUM_MAX_LENGTH,
    'utm_campaign': UTM_CAMPAIGN_MAX_LENGTH,
}


def sanitize_utm_value(value, max_length):
    text = str(value or '')
    text = ''.join(
        ch for ch in text
        if ch.isprintable() and ch not in '\r\n\t'
    )
    text = ' '.join(text.split())
    return text[: int(max_length)]


def empty_utm_snapshot():
    return {
        'utm_source': '',
        'utm_medium': '',
        'utm_campaign': '',
    }


def get_utm_snapshot(request):
    stored = request.session.get(SESSION_UTM_KEY) or {}
    snapshot = empty_utm_snapshot()
    for key, max_length in _UTM_MAX_LENGTHS.items():
        snapshot[key] = sanitize_utm_value(stored.get(key, ''), max_length)
    return snapshot


def capture_utm_from_request(request):
    """Save non-empty utm_* GET params. Empty values do not overwrite."""
    stored = dict(request.session.get(SESSION_UTM_KEY) or {})
    changed = False
    for key, max_length in _UTM_MAX_LENGTHS.items():
        if key not in request.GET:
            continue
        cleaned = sanitize_utm_value(request.GET.get(key), max_length)
        if not cleaned:
            continue
        if stored.get(key) != cleaned:
            stored[key] = cleaned
            changed = True
    if changed:
        request.session[SESSION_UTM_KEY] = stored
        request.session.modified = True
    return get_utm_snapshot(request)


def clear_utm(request):
    if SESSION_UTM_KEY in request.session:
        request.session.pop(SESSION_UTM_KEY, None)
        request.session.modified = True
