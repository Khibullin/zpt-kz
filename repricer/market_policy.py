"""Freshness and price floors shared by monitoring and Kaspi export."""
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .competitor_display import competitor_fresh_minutes

POLICY_VERSION = 'repricer_category_identity_v2'


def is_fresh(value, now=None):
    now = now or timezone.now()
    return bool(value and timezone.is_aware(value) and now - timedelta(minutes=competitor_fresh_minutes()) <= value <= now + timedelta(minutes=5))


def observation_time(items, explicit=None):
    times = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = next((item.get(key) for key in ('scrapedAt', 'observedAt', 'capturedAt') if item.get(key)), None)
        value = parse_datetime(str(raw)) if raw else None
        if value and timezone.is_aware(value):
            times.append(value)
        elif explicit is None:
            raise ValueError('В JSON нет даты проверки. Укажите дату и время запуска Apify при загрузке.')
    if explicit is not None:
        times.append(explicit)
    clock = min(times) if times else None
    if not clock or timezone.is_naive(clock) or clock > timezone.now() + timedelta(minutes=5):
        raise ValueError('Некорректная дата проверки Apify.')
    return clock


def scope_category(name):
    # Kaspi category IDs/slugs verified previously. No unverified leaf guesses.
    return 'Anti Roll Bars' if 'стабилизатор' in str(name or '').casefold() else 'Replacement parts'


def floor_records(batch):
    from procurement.models import Activity
    result = {}
    for record in Activity.objects.filter(action='repricer_price_floor', details__batch_id=batch.pk).order_by('pk'):
        result[str(record.details.get('row_id'))] = record.details.get('price')
    return result


def price_floor(record, manual, user=None):
    """Use an explicit operator MIN, protected by known cost. Never invent fees."""
    floor = Decimal(str(manual or 0))
    product = record.listing.product if record.listing_id else None
    cost = Decimal(str(product.cost_price or 0)) if product else Decimal(0)
    if record.listing_id:
        from .models import KaspiRepricerRule
        rule = KaspiRepricerRule.objects.filter(listing_id=record.listing_id).first()
        if rule and rule.is_enabled:
            floor = max(floor, rule.min_price)
    # Configured financial preferences may establish a cost-based break-even.
    if cost > 0 and user is not None:
        from procurement.models import Preferences
        prefs = Preferences.objects.filter(user=user).first()
        if prefs and prefs.commission is not None and prefs.expenses is not None and Decimal(0) <= prefs.commission < Decimal(100):
            floor = max(floor, (cost + max(Decimal(0), prefs.expenses)) / (1 - prefs.commission / 100))
    return int(max(floor, cost).to_integral_value(rounding=ROUND_CEILING)) if floor > 0 else None
