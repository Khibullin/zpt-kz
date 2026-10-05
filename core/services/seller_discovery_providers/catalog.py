"""Geography and search directions for Seller Discovery.

2GIS search uses one geographic search area per city: a center point plus a
radius. That circle is not an administrative city boundary and is not a shop
address. Coordinates live only in the city registries below. Region ids are
not used. DISCOVERY_CITIES stays the original three-city MVP.
KZ_DISCOVERY_CITIES adds regional centers. Coordinates are Wikipedia
city-center points.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.services.seller_discovery_providers.base import DiscoveryProviderError

NEW_PARTS_DIRECTIONS: tuple[str, ...] = (
    'автозапчасти',
    'магазин автозапчастей',
    'оптовые автозапчасти',
    'запчасти для китайских автомобилей',
    'Chery запчасти',
    'Haval запчасти',
    'Geely запчасти',
    'Jetour запчасти',
    'масла',
    'фильтры',
    'кузовные запчасти',
    'ходовая часть',
    'двигатели',
    'автоэлектрика',
    'оптика',
)
DISMANTLER_DIRECTIONS: tuple[str, ...] = (
    'авторазбор',
    'авторазборка',
    'разбор автомобилей',
    'б/у автозапчасти',
    'контрактные запчасти',
    'запчасти с разбора',
)
# Original order stays first so the default query cap still runs the same
# first directions. Extra phrases are appended.
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
    'оптовые автозапчасти',
    'авторазборка',
    'разбор автомобилей',
    'б/у автозапчасти',
    'контрактные запчасти',
    'запчасти с разбора',
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

# Wikipedia city-center coordinates. radius_m is a search circle, not a boundary.
_KZ_EXTRA_CITIES: dict[str, DiscoveryCity] = {
    'Караганда': DiscoveryCity(
        name='Караганда',
        aliases=('караганда', 'karaganda', 'karagandy', 'qaragandy'),
        longitude=73.10556,
        latitude=49.80278,
        radius_m=18000,
    ),
    'Актобе': DiscoveryCity(
        name='Актобе',
        aliases=('актобе', 'aktobe', 'aqtobe', 'aktyubinsk'),
        longitude=57.22972,
        latitude=50.28361,
        radius_m=18000,
    ),
    'Тараз': DiscoveryCity(
        name='Тараз',
        aliases=('тараз', 'taraz'),
        longitude=71.367,
        latitude=42.900,
        radius_m=15000,
    ),
    'Павлодар': DiscoveryCity(
        name='Павлодар',
        aliases=('павлодар', 'pavlodar'),
        longitude=76.956389,
        latitude=52.315556,
        radius_m=15000,
    ),
    'Усть-Каменогорск': DiscoveryCity(
        name='Усть-Каменогорск',
        aliases=('усть-каменогорск', 'усть каменогорск', 'oskemen', 'öskemen', 'ust-kamenogorsk'),
        longitude=82.617,
        latitude=49.983,
        radius_m=15000,
    ),
    'Семей': DiscoveryCity(
        name='Семей',
        aliases=('семей', 'semey', 'semei', 'semipalatinsk'),
        longitude=80.26667,
        latitude=50.43333,
        radius_m=15000,
    ),
    'Костанай': DiscoveryCity(
        name='Костанай',
        aliases=('костанай', 'kostanay', 'kostanai', 'qostanai'),
        longitude=63.62,
        latitude=53.20,
        radius_m=15000,
    ),
    'Кызылорда': DiscoveryCity(
        name='Кызылорда',
        aliases=('кызылорда', 'kyzylorda', 'qyzylorda'),
        longitude=65.499889,
        latitude=44.847900,
        radius_m=15000,
    ),
    'Атырау': DiscoveryCity(
        name='Атырау',
        aliases=('атырау', 'atyrau'),
        longitude=51.88333,
        latitude=47.11667,
        radius_m=15000,
    ),
    'Актау': DiscoveryCity(
        name='Актау',
        aliases=('актау', 'aktau', 'aqtau'),
        longitude=51.15750,
        latitude=43.65250,
        radius_m=15000,
    ),
    'Уральск': DiscoveryCity(
        name='Уральск',
        aliases=('уральск', 'oral', 'uralsk'),
        longitude=51.37250,
        latitude=51.22250,
        radius_m=15000,
    ),
    'Петропавловск': DiscoveryCity(
        name='Петропавловск',
        aliases=('петропавловск', 'петропавл', 'petropavl', 'petropavlovsk'),
        longitude=69.167,
        latitude=54.883,
        radius_m=12000,
    ),
    'Туркестан': DiscoveryCity(
        name='Туркестан',
        aliases=('туркестан', 'turkistan', 'turkestan'),
        longitude=68.26917,
        latitude=43.30194,
        radius_m=12000,
    ),
    'Талдыкорган': DiscoveryCity(
        name='Талдыкорган',
        aliases=('талдыкорган', 'taldykorgan', 'taldyqorgan'),
        longitude=78.36667,
        latitude=45.01667,
        radius_m=12000,
    ),
    'Кокшетау': DiscoveryCity(
        name='Кокшетау',
        aliases=('кокшетау', 'kokshetau'),
        longitude=69.383,
        latitude=53.283,
        radius_m=12000,
    ),
    'Жезказган': DiscoveryCity(
        name='Жезказган',
        aliases=('жезказган', 'zhezkazgan', 'jezkazgan'),
        longitude=67.70000,
        latitude=47.78333,
        radius_m=12000,
    ),
    'Конаев': DiscoveryCity(
        name='Конаев',
        aliases=('конаев', 'qonaev', 'qonayev', 'konayev', 'капчагай', 'kapchagay'),
        longitude=77.08333,
        latitude=43.88333,
        radius_m=12000,
    ),
}

KZ_DISCOVERY_CITIES: dict[str, DiscoveryCity] = {**DISCOVERY_CITIES, **_KZ_EXTRA_CITIES}
KZ_DISCOVERY_CITY_NAMES: tuple[str, ...] = tuple(KZ_DISCOVERY_CITIES)


def resolve_city(name: str) -> DiscoveryCity:
    raw = str(name or '').strip()
    if not raw:
        raise DiscoveryProviderError('Город discovery не указан.')
    if raw in KZ_DISCOVERY_CITIES:
        return KZ_DISCOVERY_CITIES[raw]
    folded = raw.casefold()
    for city in KZ_DISCOVERY_CITIES.values():
        if folded == city.name.casefold() or folded in city.aliases:
            return city
    raise DiscoveryProviderError(f'Город {raw} не входит в реестр discovery Казахстана.')


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
