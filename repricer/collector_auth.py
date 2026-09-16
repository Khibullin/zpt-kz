from __future__ import annotations

import hmac
from functools import wraps

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt


def configured_collector_token() -> str:
    return str(getattr(settings, "KASPI_COMPETITOR_COLLECTOR_TOKEN", "") or "").strip()


def _bearer_token(request) -> str:
    header = request.META.get("HTTP_AUTHORIZATION") or ""
    prefix = "bearer "
    if not header.lower().startswith(prefix):
        return ""
    return header[len(prefix) :].strip()


def collector_token_matches(request) -> bool:
    expected = configured_collector_token()
    provided = _bearer_token(request)
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def collector_auth_required(view):
    """Bearer-only machine auth. No staff/session fallback."""

    @csrf_exempt
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not configured_collector_token():
            response = JsonResponse({"error": "collector_unavailable"}, status=503)
            response["X-Robots-Tag"] = "noindex, nofollow"
            return response
        if not collector_token_matches(request):
            response = JsonResponse({"error": "unauthorized"}, status=401)
            response["X-Robots-Tag"] = "noindex, nofollow"
            return response
        return view(request, *args, **kwargs)

    return wrapped
