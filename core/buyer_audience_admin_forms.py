from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from core.models import (
    REQUEST_SEARCH_SCOPE_CHOICES,
    TRANSPORT_CHOICES,
    BuyerAudience,
    BuyerCategoryInterest,
    BuyerContact,
    BuyerVehicle,
)
from core.services.buyer_audience_service import (
    AUDIENCE_ACTIVITY_DAYS,
    AUDIENCE_ACTIVITY_LAST_7_DAYS,
    AUDIENCE_ACTIVITY_LAST_30_DAYS,
    AUDIENCE_ACTIVITY_LAST_60_DAYS,
    AUDIENCE_ACTIVITY_LAST_90_DAYS,
    AUDIENCE_ACTIVITY_LAST_180_DAYS,
    AUDIENCE_ACTIVITY_NO_ACTIVITY_DATE,
    AUDIENCE_ACTIVITY_OLDER_THAN_180_DAYS,
    AUDIENCE_ACTIVITY_PERIODS,
    audience_criteria_has_filters,
    normalize_audience_criteria,
)
from core.services.buyer_contact_utils import normalize_buyer_text

ACTIVITY_PERIOD_CHOICES = [
    ('', 'Без ограничения'),
    (AUDIENCE_ACTIVITY_LAST_7_DAYS, 'Последние 7 дней'),
    (AUDIENCE_ACTIVITY_LAST_30_DAYS, 'Последние 30 дней'),
    (AUDIENCE_ACTIVITY_LAST_60_DAYS, 'Последние 60 дней'),
    (AUDIENCE_ACTIVITY_LAST_90_DAYS, 'Последние 90 дней'),
    (AUDIENCE_ACTIVITY_LAST_180_DAYS, 'Последние 180 дней'),
    (AUDIENCE_ACTIVITY_OLDER_THAN_180_DAYS, 'Более 180 дней назад'),
    (AUDIENCE_ACTIVITY_NO_ACTIVITY_DATE, 'Без даты активности'),
]


def _distinct_choice_values(field_name: str) -> list[str]:
    return sorted(
        {
            value.strip()
            for value in BuyerContact.objects.exclude(**{field_name: ''})
            .values_list(field_name, flat=True)
            .distinct()
            if str(value or '').strip()
        },
        key=str.casefold,
    )


def _normalized_vehicle_choices(
    *,
    normalized_field: str,
    display_field: str,
) -> list[tuple[str, str]]:
    grouped: dict[str, str] = {}
    rows = (
        BuyerVehicle.objects.exclude(**{display_field: ''})
        .exclude(**{normalized_field: ''})
        .order_by('-last_seen_at', '-id')
    )
    for row in rows:
        normalized = getattr(row, normalized_field)
        display = str(getattr(row, display_field) or '').strip()
        if not normalized or not display:
            continue
        grouped.setdefault(normalized, display)
    return sorted(grouped.items(), key=lambda item: item[1].casefold())


def _normalized_category_choices() -> list[tuple[str, str]]:
    grouped: dict[str, str] = {}
    rows = (
        BuyerCategoryInterest.objects.exclude(category='')
        .exclude(category_normalized='')
        .order_by('-last_seen_at', '-id')
    )
    for row in rows:
        if not row.category_normalized or not row.category:
            continue
        grouped.setdefault(row.category_normalized, row.category.strip())
    return sorted(grouped.items(), key=lambda item: item[1].casefold())


def criteria_to_form_initial(criteria: dict) -> dict[str, object]:
    normalized = normalize_audience_criteria(criteria)
    return {
        'countries': normalized['countries'],
        'cities': normalized['cities'],
        'transport_types': normalized['transport_types'],
        'brands': normalized['brands'],
        'models': normalized['models'],
        'categories': normalized['categories'],
        'search_scopes': normalized['search_scopes'],
        'activity_period': normalized['activity_period'],
        'request_count_min': normalized['request_count_min'],
        'request_count_max': normalized['request_count_max'],
    }


def form_cleaned_data_to_criteria(cleaned_data: dict) -> dict:
    activity_period = cleaned_data.get('activity_period') or ''
    if activity_period not in AUDIENCE_ACTIVITY_PERIODS:
        activity_period = ''

    return normalize_audience_criteria({
        'countries': cleaned_data.get('countries') or [],
        'cities': cleaned_data.get('cities') or [],
        'transport_types': cleaned_data.get('transport_types') or [],
        'brands': cleaned_data.get('brands') or [],
        'models': cleaned_data.get('models') or [],
        'categories': cleaned_data.get('categories') or [],
        'search_scopes': cleaned_data.get('search_scopes') or [],
        'activity_period': activity_period,
        'request_count_min': cleaned_data.get('request_count_min'),
        'request_count_max': cleaned_data.get('request_count_max'),
    })


def format_criteria_summary(criteria: dict, *, max_length: int = 150) -> str:
    normalized = normalize_audience_criteria(criteria)
    if not audience_criteria_has_filters(normalized):
        return 'Все покупатели'

    parts: list[str] = []
    if normalized['cities']:
        parts.append(', '.join(normalized['cities'][:2]))
    if normalized['countries']:
        parts.append(', '.join(normalized['countries'][:1]))
    if normalized['brands']:
        parts.append(', '.join(normalized['brands'][:2]))
    if normalized['categories']:
        parts.append(', '.join(normalized['categories'][:2]))
    if normalized['transport_types']:
        labels = dict(TRANSPORT_CHOICES)
        parts.append(
            ', '.join(labels.get(value, value) for value in normalized['transport_types']),
        )
    if normalized['search_scopes']:
        labels = dict(REQUEST_SEARCH_SCOPE_CHOICES)
        parts.append(
            ', '.join(labels.get(value, value) for value in normalized['search_scopes']),
        )
    if normalized['activity_period']:
        labels = dict(ACTIVITY_PERIOD_CHOICES)
        parts.append(labels.get(normalized['activity_period'], normalized['activity_period']))
    if (
        normalized['request_count_min'] is not None
        or normalized['request_count_max'] is not None
    ):
        min_value = normalized['request_count_min']
        max_value = normalized['request_count_max']
        if min_value is not None and max_value is not None:
            parts.append(f'{min_value}–{max_value} заявок')
        elif min_value is not None:
            parts.append(f'от {min_value} заявок')
        elif max_value is not None:
            parts.append(f'до {max_value} заявок')

    summary = '; '.join(part for part in parts if part)
    if len(summary) > max_length:
        return f'{summary[: max_length - 3]}...'
    return summary


def format_criteria_details(criteria: dict) -> list[tuple[str, str]]:
    normalized = normalize_audience_criteria(criteria)
    if not audience_criteria_has_filters(normalized):
        return [('Критерии', 'Все покупатели')]

    labels = dict(ACTIVITY_PERIOD_CHOICES)
    scope_labels = dict(REQUEST_SEARCH_SCOPE_CHOICES)
    transport_labels = dict(TRANSPORT_CHOICES)
    rows = [
        ('Страны', ', '.join(normalized['countries']) or '—'),
        ('Города', ', '.join(normalized['cities']) or '—'),
        (
            'Тип транспорта',
            ', '.join(
                transport_labels.get(value, value)
                for value in normalized['transport_types']
            ) or '—',
        ),
        ('Марки', ', '.join(normalized['brands']) or '—'),
        ('Модели', ', '.join(normalized['models']) or '—'),
        ('Категории', ', '.join(normalized['categories']) or '—'),
        (
            'Режим поиска',
            ', '.join(
                scope_labels.get(value, value)
                for value in normalized['search_scopes']
            ) or '—',
        ),
        (
            'Активность',
            labels.get(normalized['activity_period'], 'Без ограничения')
            if normalized['activity_period']
            else 'Без ограничения',
        ),
        (
            'Количество заявок',
            _format_request_count_range(
                normalized['request_count_min'],
                normalized['request_count_max'],
            ),
        ),
    ]
    return rows


def _format_request_count_range(min_value: int | None, max_value: int | None) -> str:
    if min_value is None and max_value is None:
        return '—'
    if min_value is not None and max_value is not None:
        return f'от {min_value} до {max_value}'
    if min_value is not None:
        return f'от {min_value}'
    return f'до {max_value}'


class BuyerAudienceAdminForm(forms.ModelForm):
    countries = forms.MultipleChoiceField(
        required=False,
        label='Страны',
        widget=forms.SelectMultiple(attrs={'size': 8}),
    )
    cities = forms.MultipleChoiceField(
        required=False,
        label='Города',
        widget=forms.SelectMultiple(attrs={'size': 8}),
    )
    transport_types = forms.MultipleChoiceField(
        required=False,
        label='Тип транспорта',
        choices=TRANSPORT_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )
    brands = forms.MultipleChoiceField(
        required=False,
        label='Марки',
        widget=forms.SelectMultiple(attrs={'size': 8}),
    )
    models = forms.MultipleChoiceField(
        required=False,
        label='Модели',
        widget=forms.SelectMultiple(attrs={'size': 8}),
    )
    categories = forms.MultipleChoiceField(
        required=False,
        label='Категории',
        widget=forms.SelectMultiple(attrs={'size': 8}),
    )
    search_scopes = forms.MultipleChoiceField(
        required=False,
        label='Режим поиска',
        choices=REQUEST_SEARCH_SCOPE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )
    activity_period = forms.ChoiceField(
        required=False,
        label='Активность',
        choices=ACTIVITY_PERIOD_CHOICES,
    )
    request_count_min = forms.IntegerField(
        required=False,
        min_value=0,
        label='Минимум заявок',
    )
    request_count_max = forms.IntegerField(
        required=False,
        min_value=0,
        label='Максимум заявок',
    )

    class Meta:
        model = BuyerAudience
        fields = ('name', 'description', 'is_active')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['countries'].choices = [
            (value, value) for value in _distinct_choice_values('primary_country')
        ]
        self.fields['cities'].choices = [
            (value, value) for value in _distinct_choice_values('primary_city')
        ]
        self.fields['brands'].choices = _normalized_vehicle_choices(
            normalized_field='brand_normalized',
            display_field='brand',
        )
        self.fields['models'].choices = _normalized_vehicle_choices(
            normalized_field='model_normalized',
            display_field='model',
        )
        self.fields['categories'].choices = _normalized_category_choices()

        if self.instance and self.instance.pk:
            initial = criteria_to_form_initial(self.instance.criteria)
            for field_name, value in initial.items():
                self.initial[field_name] = value

    def clean(self):
        cleaned_data = super().clean()
        min_value = cleaned_data.get('request_count_min')
        max_value = cleaned_data.get('request_count_max')
        if (
            min_value is not None
            and max_value is not None
            and min_value > max_value
        ):
            raise ValidationError(
                'Минимум заявок не может быть больше максимума.',
            )

        allowed_countries = {value for value, _ in self.fields['countries'].choices}
        allowed_cities = {value for value, _ in self.fields['cities'].choices}
        allowed_brands = {value for value, _ in self.fields['brands'].choices}
        allowed_models = {value for value, _ in self.fields['models'].choices}
        allowed_categories = {value for value, _ in self.fields['categories'].choices}

        cleaned_data['countries'] = [
            value for value in cleaned_data.get('countries') or []
            if value in allowed_countries
        ]
        cleaned_data['cities'] = [
            value for value in cleaned_data.get('cities') or []
            if value in allowed_cities
        ]
        cleaned_data['brands'] = [
            value for value in cleaned_data.get('brands') or []
            if value in allowed_brands
        ]
        cleaned_data['models'] = [
            value for value in cleaned_data.get('models') or []
            if value in allowed_models
        ]
        cleaned_data['categories'] = [
            value for value in cleaned_data.get('categories') or []
            if value in allowed_categories
        ]
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.criteria = form_cleaned_data_to_criteria(self.cleaned_data)
        if commit:
            instance.save()
        return instance
