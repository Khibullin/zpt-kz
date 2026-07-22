from __future__ import annotations

SERVICE_REQUEST_CITY_DISTRICTS: dict[str, tuple[str, ...]] = {
    'Алматы': (
        'Алмалинский',
        'Алатауский',
        'Ауэзовский',
        'Бостандыкский',
        'Жетысуский',
        'Медеуский',
        'Наурызбайский',
        'Турксибский',
    ),
    'Астана': (
        'Алматы',
        'Байконыр',
        'Есиль',
        'Нура',
        'Сарайшык',
    ),
}


def normalize_service_request_location(city: str, district: str) -> tuple[str, str]:
    normalized_city = (city or '').strip()
    normalized_district = (district or '').strip()

    if not normalized_city:
        raise ValueError('Укажите город.')

    allowed = SERVICE_REQUEST_CITY_DISTRICTS.get(normalized_city)
    if allowed is None:
        return normalized_city, ''

    if not normalized_district:
        raise ValueError('Выберите район для выбранного города.')

    if normalized_district not in allowed:
        raise ValueError('Выберите корректный район для выбранного города.')

    return normalized_city, normalized_district
