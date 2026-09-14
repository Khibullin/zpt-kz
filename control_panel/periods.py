from __future__ import annotations

from datetime import datetime, time, timedelta

from django.utils import timezone

PERIOD_TODAY = 'today'
PERIOD_7D = '7d'
PERIOD_30D = '30d'
PERIOD_ALL = 'all'

PERIOD_CHOICES = (
    (PERIOD_TODAY, 'Сегодня'),
    (PERIOD_7D, '7 дней'),
    (PERIOD_30D, '30 дней'),
)

LIST_PERIOD_CHOICES = PERIOD_CHOICES + ((PERIOD_ALL, 'Все время'),)

DEFAULT_OVERVIEW_PERIOD = PERIOD_7D
DEFAULT_LIST_PERIOD = PERIOD_ALL


def normalize_period(value: object, *, default: str, allowed: tuple[str, ...]) -> str:
    raw = str(value or '').strip()
    if raw in allowed:
        return raw
    return default


def period_bounds(period: str) -> tuple[datetime | None, datetime]:
    now = timezone.now()
    if period == PERIOD_ALL:
        return None, now
    today = timezone.localdate()
    if period == PERIOD_7D:
        start_date = today - timedelta(days=6)
    elif period == PERIOD_30D:
        start_date = today - timedelta(days=29)
    else:
        start_date = today
    start = timezone.make_aware(
        datetime.combine(start_date, time.min),
        timezone.get_current_timezone(),
    )
    return start, now


def apply_created_range(queryset, field_name: str, period: str):
    start, end = period_bounds(period)
    lookup_end = {f'{field_name}__lte': end}
    if start is None:
        return queryset.filter(**lookup_end)
    return queryset.filter(**{f'{field_name}__gte': start}, **lookup_end)
