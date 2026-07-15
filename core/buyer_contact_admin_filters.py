from __future__ import annotations

from datetime import timedelta

from django.contrib import admin
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from core.models import (
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    BuyerCategoryInterest,
    BuyerContact,
    BuyerVehicle,
    ContactConsent,
)
from core.services.buyer_contact_utils import normalize_buyer_text

MARKETING_CONSENT_LABELS = {
    CONTACT_CONSENT_STATUS_GRANTED: 'Разрешено',
    CONTACT_CONSENT_STATUS_REVOKED: 'Отозвано',
    'unknown': 'Не подтверждено',
}

PRIMARY_CITY_EMPTY = '__empty__'


def marketing_consent_label(status: str | None) -> str:
    if not status:
        return MARKETING_CONSENT_LABELS['unknown']
    return MARKETING_CONSENT_LABELS.get(status, MARKETING_CONSENT_LABELS['unknown'])


def format_limited_summary(items: list[str], limit: int = 3) -> str:
    if not items:
        return ''
    shown = items[:limit]
    text = ', '.join(shown)
    remaining = len(items) - limit
    if remaining > 0:
        text = f'{text} +{remaining}'
    return text


def build_vehicle_summary(vehicles) -> str:
    labels: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for vehicle in vehicles:
        key = (
            vehicle.transport_type,
            vehicle.brand_normalized,
            vehicle.model_normalized,
        )
        if key in seen:
            continue
        seen.add(key)
        label = f'{vehicle.brand} {vehicle.model}'.strip()
        if label:
            labels.append(label)
    return format_limited_summary(labels)


def build_category_summary(interests) -> str:
    labels: list[str] = []
    seen: set[str] = set()
    for interest in interests:
        if interest.category_normalized in seen:
            continue
        seen.add(interest.category_normalized)
        if interest.category:
            labels.append(interest.category)
    return format_limited_summary(labels)


def _grouped_lookup_values(
    rows,
    *,
    normalized_field: str,
    display_field: str,
) -> list[tuple[str, str]]:
    grouped: dict[str, str] = {}
    for row in rows:
        normalized = getattr(row, normalized_field)
        display = getattr(row, display_field)
        if not normalized or not str(display).strip():
            continue
        grouped.setdefault(normalized, str(display).strip())
    return sorted(
        grouped.items(),
        key=lambda item: item[1].casefold(),
    )


def _primary_city_lookups() -> list[tuple[str, str]]:
    grouped: dict[str, str] = {}
    for city in (
        BuyerContact.objects.exclude(primary_city='')
        .values_list('primary_city', flat=True)
        .distinct()
    ):
        normalized = normalize_buyer_text(city)
        if not normalized:
            continue
        grouped.setdefault(normalized, city.strip())
    lookups = sorted(grouped.items(), key=lambda item: item[1].casefold())
    return [(PRIMARY_CITY_EMPTY, 'Без города'), *lookups]


def _primary_city_variants(normalized_value: str) -> list[str]:
    return [
        city
        for city in BuyerContact.objects.exclude(primary_city='')
        .values_list('primary_city', flat=True)
        .distinct()
        if normalize_buyer_text(city) == normalized_value
    ]


class BuyerPrimaryCityFilter(admin.SimpleListFilter):
    title = 'Город'
    parameter_name = 'primary_city'

    def lookups(self, request, model_admin):
        return _primary_city_lookups()

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        if self.value() == PRIMARY_CITY_EMPTY:
            return queryset.filter(primary_city='')
        variants = _primary_city_variants(self.value())
        if not variants:
            return queryset.none()
        return queryset.filter(primary_city__in=variants)


class BuyerTransportTypeFilter(admin.SimpleListFilter):
    title = 'Тип транспорта'
    parameter_name = 'transport_type'

    def lookups(self, request, model_admin):
        return (
            ('car', 'Легковые'),
            ('truck', 'Грузовые'),
        )

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        return queryset.filter(
            vehicles__transport_type=self.value(),
        ).distinct()


class BuyerBrandFilter(admin.SimpleListFilter):
    title = 'Марка'
    parameter_name = 'vehicle_brand'

    def lookups(self, request, model_admin):
        rows = (
            BuyerVehicle.objects.exclude(brand='')
            .exclude(brand_normalized='')
            .order_by('-last_seen_at', '-id')
        )
        return _grouped_lookup_values(
            rows,
            normalized_field='brand_normalized',
            display_field='brand',
        )

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        return queryset.filter(
            vehicles__brand_normalized=self.value(),
        ).distinct()


class BuyerModelFilter(admin.SimpleListFilter):
    title = 'Модель'
    parameter_name = 'vehicle_model'

    def lookups(self, request, model_admin):
        rows = (
            BuyerVehicle.objects.exclude(model='')
            .exclude(model_normalized='')
            .order_by('-last_seen_at', '-id')
        )
        return _grouped_lookup_values(
            rows,
            normalized_field='model_normalized',
            display_field='model',
        )

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        return queryset.filter(
            vehicles__model_normalized=self.value(),
        ).distinct()


class BuyerCategoryFilter(admin.SimpleListFilter):
    title = 'Категория'
    parameter_name = 'category'

    def lookups(self, request, model_admin):
        rows = (
            BuyerCategoryInterest.objects.exclude(category='')
            .exclude(category_normalized='')
            .order_by('-last_seen_at', '-id')
        )
        return _grouped_lookup_values(
            rows,
            normalized_field='category_normalized',
            display_field='category',
        )

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        return queryset.filter(
            category_interests__category_normalized=self.value(),
        ).distinct()


class BuyerActivityFilter(admin.SimpleListFilter):
    title = 'Активность'
    parameter_name = 'activity'

    def lookups(self, request, model_admin):
        return (
            ('last_7', 'Последние 7 дней'),
            ('last_30', 'Последние 30 дней'),
            ('last_60', 'Последние 60 дней'),
            ('last_90', 'Последние 90 дней'),
            ('last_180', 'Последние 180 дней'),
            ('over_180', 'Более 180 дней назад'),
            ('no_date', 'Без даты активности'),
        )

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        now = timezone.now()
        if self.value() == 'no_date':
            return queryset.filter(last_request_at__isnull=True)
        if self.value() == 'over_180':
            return queryset.filter(
                last_request_at__lt=now - timedelta(days=180),
            )
        days_map = {
            'last_7': 7,
            'last_30': 30,
            'last_60': 60,
            'last_90': 90,
            'last_180': 180,
        }
        days = days_map.get(self.value())
        if days is None:
            return queryset
        return queryset.filter(
            last_request_at__gte=now - timedelta(days=days),
        )


class BuyerRequestCountFilter(admin.SimpleListFilter):
    title = 'Количество заявок'
    parameter_name = 'requests_count_range'

    def lookups(self, request, model_admin):
        return (
            ('1', '1 заявка'),
            ('2_4', '2–4 заявки'),
            ('5_9', '5–9 заявок'),
            ('10_49', '10–49 заявок'),
            ('50_plus', '50 и более'),
        )

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        if self.value() == '1':
            return queryset.filter(requests_count=1)
        if self.value() == '2_4':
            return queryset.filter(requests_count__gte=2, requests_count__lte=4)
        if self.value() == '5_9':
            return queryset.filter(requests_count__gte=5, requests_count__lte=9)
        if self.value() == '10_49':
            return queryset.filter(requests_count__gte=10, requests_count__lte=49)
        if self.value() == '50_plus':
            return queryset.filter(requests_count__gte=50)
        return queryset


class BuyerMarketingConsentFilter(admin.SimpleListFilter):
    title = 'Рекламное согласие'
    parameter_name = 'marketing_consent'

    def lookups(self, request, model_admin):
        return (
            (CONTACT_CONSENT_STATUS_GRANTED, 'Разрешено'),
            ('unknown', 'Не подтверждено'),
            (CONTACT_CONSENT_STATUS_REVOKED, 'Отозвано'),
        )

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        marketing_consent = ContactConsent.objects.filter(
            buyer=OuterRef('pk'),
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        )
        if self.value() == CONTACT_CONSENT_STATUS_GRANTED:
            return queryset.filter(
                Exists(
                    marketing_consent.filter(status=CONTACT_CONSENT_STATUS_GRANTED),
                ),
            )
        if self.value() == CONTACT_CONSENT_STATUS_REVOKED:
            return queryset.filter(
                Exists(
                    marketing_consent.filter(status=CONTACT_CONSENT_STATUS_REVOKED),
                ),
            )
        if self.value() == 'unknown':
            return queryset.filter(
                ~Exists(
                    marketing_consent.filter(
                        status__in=[
                            CONTACT_CONSENT_STATUS_GRANTED,
                            CONTACT_CONSENT_STATUS_REVOKED,
                        ],
                    ),
                ),
            )
        return queryset
