"""Capture campaign labels for parts requests independently of shop orders."""
from django.utils import timezone

SESSION_KEY = 'parts_request_campaign'
MAX_AGE_SECONDS = 7 * 24 * 60 * 60
LIMITS = {'utm_source': 100, 'utm_medium': 100, 'utm_campaign': 200}


def clean_campaign(data):
    return {
        key: ' '.join(''.join(ch for ch in str(data.get(key) or '')
                              if ch.isprintable()).split())[:limit]
        for key, limit in LIMITS.items()
    }


def capture_request_campaign(request):
    labels = clean_campaign(request.GET)
    # A new campaign replaces the whole tuple; never combine two advertisers.
    if labels['utm_source']:
        request.session[SESSION_KEY] = {
            **labels, 'captured_at': timezone.now().timestamp(),
        }


def get_request_campaign(request):
    stored = request.session.get(SESSION_KEY)
    if not isinstance(stored, dict):
        return {}
    try:
        age = timezone.now().timestamp() - float(stored.get('captured_at', 0))
    except (TypeError, ValueError, OverflowError):
        return {}
    if not 0 <= age <= MAX_AGE_SECONDS:
        return {}
    labels = clean_campaign(stored)
    return labels if labels['utm_source'] else {}
