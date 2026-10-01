"""MVP geography and search directions for Seller Discovery Package 2.

2GIS search uses one geographic search area per city: a center point plus a
radius. That circle is not an administrative city boundary and is not a shop
address. Coordinates live only in DISCOVERY_CITIES. Region ids are not used.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.services.seller_discovery_providers.base import DiscoveryProviderError

SEARCH_DIRECTIONS: tuple[str, ...] = (
    'автозапчасти',
    'магазин автозапчастей',
    'запчасти для китайских автомобилей',
    'Chery запчасти',
    'Haval запчасти',
    'Geely запчасти',
    'Jetour запчасти',
    'масла',
    'фильтры',
    'кузовные запчасти',
    'ходовая часть',
    'авторазбор',
    'двигатели',
    'автоэлектрика',
    'оптика',
)

# 2GIS /3.0/items accepts radius 0..50000 when a text query is present.
TWO_GIS_MIN_RADIUS_M = 1
TWO_GIS_MAX_RADIUS_M = 50000
TWO_GIS_PAGE_SIZE_DEFAULT = 10
TWO_GIS_PAGE_SIZE_CAP = 10
TWO_GIS_MAX_PAGES_DEFAULT = 1
TWO_GIS_MAX_PAGES_CAP = 5


@dataclass(frozen=True)
class DiscoveryCity:
    name: str
    aliases: tuple[str, ...]
    longitude: float
    latitude: float
    radius_m: int


DISCOVERY_CITIES: dict[str, DiscoveryCity] = {
    'Алматы': DiscoveryCity(
        name='Алматы',
        aliases=('алматы', 'almaty', 'алма-ата'),
        longitude=76.945465,
        latitude=43.238293,
        radius_m=18000,
    ),
    'Астана': DiscoveryCity(
        name='Астана',
        aliases=('астана', 'astana', 'нур-султан', 'нурсултан', 'nur-sultan', 'nursultan'),
        longitude=71.430411,
        latitude=51.128207,
        radius_m=18000,
    ),
    'Шымкент': DiscoveryCity(
        name='Шымкент',
        aliases=('шымкент', 'shymkent'),
        longitude=69.590073,
        latitude=42.315514,
        radius_m=15000,
    ),
}


def resolve_city(name: str) -> DiscoveryCity:
    raw = str(name or '').strip()
    if not raw:
        raise DiscoveryProviderError('Город discovery не указан.')
    if raw in DISCOVERY_CITIES:
        return DISCOVERY_CITIES[raw]
    folded = raw.casefold()
    for city in DISCOVERY_CITIES.values():
        if folded == city.name.casefold() or folded in city.aliases:
            return city
    raise DiscoveryProviderError(f'Город {raw} не входит в MVP discovery.')


def bounded_search_radius(radius_m: int) -> int:
    """Reject a radius outside the 2GIS query limit instead of searching unbounded."""
    try:
        radius = int(radius_m)
    except (TypeError, ValueError) as exc:
        raise DiscoveryProviderError('Радиус поиска 2GIS задан неверно.') from exc
    if radius < TWO_GIS_MIN_RADIUS_M or radius > TWO_GIS_MAX_RADIUS_M:
        raise DiscoveryProviderError(
            f'Радиус поиска {radius} м вне допустимого диапазона 2GIS '
            f'{TWO_GIS_MIN_RADIUS_M}..{TWO_GIS_MAX_RADIUS_M} м.',
        )
    return radius
