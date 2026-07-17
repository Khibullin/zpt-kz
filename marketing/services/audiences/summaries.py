from __future__ import annotations

from marketing.services.audiences.constants import (
    CONTACT_GROUPS,
    GROUP_SUBTYPE_MAP,
)
from marketing.services.audiences.filters import (
    ACTIVITY_PERIOD_CHOICES,
    CATEGORY_PERIOD_CHOICES,
    SEARCH_SCOPE_CHOICES,
    TRANSPORT_TYPE_CHOICES,
    normalize_marketing_criteria,
)
from service_requests.models import Service


def _label_for_value(value: str, choices: tuple[tuple[str, str], ...]) -> str:
    mapping = dict(choices)
    return mapping.get(value, value)


def criteria_summary(criteria: dict, *, contact_group: str, contact_subtype: str) -> str:
    normalized = normalize_marketing_criteria(
        criteria,
        contact_group=contact_group,
        contact_subtype=contact_subtype,
    )
    parts: list[str] = []

    group_label = dict(CONTACT_GROUPS).get(contact_group, contact_group)
    subtype_label = dict(GROUP_SUBTYPE_MAP.get(contact_group, ())).get(
        contact_subtype,
        contact_subtype,
    )
    parts.append(f'{group_label}: {subtype_label}')

    if normalized['primary_cities']:
        parts.append('Основной город: ' + ', '.join(normalized['primary_cities'][:5]))
    if normalized['search_cities']:
        parts.append('Города поиска: ' + ', '.join(normalized['search_cities'][:5]))
    if normalized['cities']:
        parts.append('Города: ' + ', '.join(normalized['cities'][:5]))
        if len(normalized['cities']) > 5:
            parts.append(f'(+{len(normalized["cities"]) - 5})')
    if normalized['countries']:
        parts.append('Страны: ' + ', '.join(normalized['countries']))
    if normalized['brands']:
        parts.append('Марки: ' + ', '.join(normalized['brands'][:5]))
    if normalized['models']:
        parts.append('Модели: ' + ', '.join(normalized['models'][:5]))
    if normalized['categories']:
        parts.append('Категории: ' + ', '.join(normalized['categories'][:5]))
    if normalized['search_scopes']:
        labels = [
            _label_for_value(value, SEARCH_SCOPE_CHOICES)
            for value in normalized['search_scopes']
        ]
        parts.append('Область поиска: ' + ', '.join(labels))
    if normalized['transport_types']:
        labels = [
            _label_for_value(value, TRANSPORT_TYPE_CHOICES)
            for value in normalized['transport_types']
        ]
        parts.append('Транспорт: ' + ', '.join(labels))
    if normalized['services']:
        service_names = list(
            Service.objects.filter(id__in=normalized['services']).values_list('name', flat=True),
        )
        if service_names:
            parts.append('Услуги: ' + ', '.join(service_names[:5]))
    if normalized['activity_period']:
        parts.append(
            'Активность: '
            + _label_for_value(normalized['activity_period'], ACTIVITY_PERIOD_CHOICES),
        )
    if normalized['category_period']:
        parts.append(
            'Период интереса: '
            + _label_for_value(normalized['category_period'], CATEGORY_PERIOD_CHOICES),
        )
    if normalized['activity_from'] or normalized['activity_to']:
        parts.append(
            f"Даты: {normalized['activity_from'] or '…'} — {normalized['activity_to'] or '…'}",
        )
    if normalized['is_active'] is True:
        parts.append('Только активные')
    if normalized['is_active'] is False:
        parts.append('Только неактивные')
    if normalized['is_test'] is True:
        parts.append('Только тестовые')
    if normalized['is_test'] is False:
        parts.append('Только реальные')

    if not any(key for key in parts[1:]):
        parts.append('Без дополнительных ограничений')
    return '; '.join(parts)


def calculation_summary_lines(result) -> list[tuple[str, int]]:
    return [
        ('Найдено по критериям', result.matched_count),
        ('Уникальных телефонов', result.unique_phones),
        ('Некорректных телефонов', result.invalid_phones),
        ('Дублей', result.duplicate_count),
        ('Тестовых контактов', result.test_count),
        ('Неактивных', result.inactive_count),
        ('С рекламным согласием', result.granted_count),
        ('Без подтверждённого согласия', result.unknown_count),
        ('С отозванным согласием', result.revoked_count),
        ('Согласие не зафиксировано', result.consent_not_recorded_count),
        ('Допустимых к отправке', result.eligible_count),
    ]
