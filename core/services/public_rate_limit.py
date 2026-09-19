"""Public rate limit for short-form homepage submissions.

Limits are stored in Postgres/SQLite (HomePartsRateBucket), so they are shared
across Gunicorn workers. LocMem Django cache is not used.

Environment (see backend/settings.py):
  HOME_PARTS_MAX_PER_HOUR          default 8; 0 disables the IP limit
  HOME_PARTS_MAX_PER_PHONE_HOUR    default 5; 0 disables the phone limit
  HOME_PARTS_RATE_LIMIT_WINDOW     seconds, default 3600
  HOME_PARTS_NUM_PROXIES           trusted proxy hops, default 1 (Render)

IP extraction uses the right-hand X-Forwarded-For hop. A client-supplied
left-hand value cannot replace the address added by the trusted proxy.
When X-Forwarded-For is absent, REMOTE_ADDR is used (local/dev).
"""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import HomePartsRateBucket
from core.phone_utils import normalize_phone_for_whatsapp


def client_ip(request) -> str:
    num_proxies = getattr(settings, 'HOME_PARTS_NUM_PROXIES', 1)
    try:
        num_proxies = int(num_proxies)
    except (TypeError, ValueError):
        num_proxies = 1
    remote = str(request.META.get('REMOTE_ADDR') or '').strip()
    forwarded = [
        part.strip()
        for part in str(request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')
        if part.strip()
    ]
    if num_proxies <= 0:
        return remote
    if not forwarded:
        return remote
    index = len(forwarded) - num_proxies
    if index < 0:
        index = 0
    return forwarded[index]


def home_parts_rate_limit_allowed(request, phone: str = '') -> bool:
    window = int(getattr(settings, 'HOME_PARTS_RATE_LIMIT_WINDOW', 3600) or 3600)
    ip_limit = getattr(settings, 'HOME_PARTS_MAX_PER_HOUR', 8)
    phone_limit = getattr(settings, 'HOME_PARTS_MAX_PER_PHONE_HOUR', 5)
    ip_limit = 8 if ip_limit is None else int(ip_limit)
    phone_limit = 5 if phone_limit is None else int(phone_limit)

    items = []
    digits = normalize_phone_for_whatsapp(phone) or ''
    if digits and phone_limit > 0:
        items.append((f'phone:{digits}', phone_limit))
    ip = client_ip(request) or 'unknown'
    if ip_limit > 0:
        items.append((f'ip:{ip}', ip_limit))
    items.sort(key=lambda item: item[0])
    return _reserve_all(items, window)


def _lock_bucket(bucket_key: str, now):
    try:
        return HomePartsRateBucket.objects.select_for_update().get(key=bucket_key)
    except HomePartsRateBucket.DoesNotExist:
        try:
            # Nested atomic is a SAVEPOINT so a unique-key race rolls back
            # only the INSERT. The outer transaction stays usable for the
            # following select_for_update().
            with transaction.atomic():
                return HomePartsRateBucket.objects.create(
                    key=bucket_key,
                    hits=0,
                    window_started_at=now,
                )
        except IntegrityError:
            return HomePartsRateBucket.objects.select_for_update().get(key=bucket_key)


def _reserve_all(items: list[tuple[str, int]], window: int) -> bool:
    if not items:
        return True
    now = timezone.now()
    with transaction.atomic():
        updates = []
        for bucket_key, limit in items:
            bucket = _lock_bucket(bucket_key, now)
            started = bucket.window_started_at
            hits = bucket.hits
            if started is None or now - started >= timedelta(seconds=window):
                hits = 0
                started = now
            if hits >= limit:
                return False
            updates.append((bucket, hits + 1, started))
        for bucket, hits, started in updates:
            bucket.hits = hits
            bucket.window_started_at = started
            bucket.save(update_fields=['hits', 'window_started_at'])
    return True
