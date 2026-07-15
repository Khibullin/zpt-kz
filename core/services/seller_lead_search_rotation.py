from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.utils import timezone

ROTATION_EPOCH = date(2026, 7, 15)


@dataclass(frozen=True)
class RotationProfile:
    slug: str
    search_term: str
    category: str


SEARCH_ROTATION_PROFILES: tuple[RotationProfile, ...] = (
    RotationProfile(slug='general_parts', search_term='автозапчасти', category='автозапчасти'),
    RotationProfile(slug='auto_dismantling', search_term='авторазбор', category='авторазбор'),
    RotationProfile(
        slug='wholesale_parts',
        search_term='автозапчасти оптом',
        category='автозапчасти',
    ),
    RotationProfile(slug='bmw_parts', search_term='запчасти BMW', category='автозапчасти'),
    RotationProfile(
        slug='mercedes_parts',
        search_term='запчасти Mercedes-Benz',
        category='автозапчасти',
    ),
    RotationProfile(
        slug='korean_parts',
        search_term='корейские автозапчасти',
        category='автозапчасти',
    ),
    RotationProfile(
        slug='japanese_parts',
        search_term='японские автозапчасти',
        category='автозапчасти',
    ),
    RotationProfile(
        slug='chinese_parts',
        search_term='китайские автозапчасти',
        category='автозапчасти',
    ),
    RotationProfile(
        slug='truck_parts',
        search_term='грузовые автозапчасти',
        category='автозапчасти',
    ),
    RotationProfile(slug='toyota_parts', search_term='запчасти Toyota', category='автозапчасти'),
    RotationProfile(
        slug='hyundai_kia_parts',
        search_term='запчасти Hyundai Kia',
        category='автозапчасти',
    ),
    RotationProfile(
        slug='vag_parts',
        search_term='запчасти Volkswagen Audi',
        category='автозапчасти',
    ),
    RotationProfile(
        slug='body_parts',
        search_term='кузовные запчасти',
        category='автозапчасти',
    ),
    RotationProfile(slug='used_parts', search_term='автозапчасти б/у', category='автозапчасти'),
)


@dataclass(frozen=True)
class ResolvedPipelineSearch:
    search_term: str
    category: str
    rotation_enabled: bool = False
    rotation_slug: str = ''
    rotation_index: int | None = None


class PipelineSearchConfigError(ValueError):
    """Ошибка конфигурации search_term / category pipeline."""


def get_rotation_profile(
    target_date: date | None = None,
) -> tuple[RotationProfile, int]:
    """Return rotation profile and zero-based index for the given local date."""
    current_date = target_date or timezone.localdate()
    day_offset = (current_date - ROTATION_EPOCH).days
    index = day_offset % len(SEARCH_ROTATION_PROFILES)
    return SEARCH_ROTATION_PROFILES[index], index


def resolve_pipeline_search(
    *,
    category: str,
    search_term: str | None = None,
    rotate_search_term: bool = False,
    target_date: date | None = None,
) -> ResolvedPipelineSearch:
    if rotate_search_term and search_term:
        raise PipelineSearchConfigError(
            'Нельзя одновременно использовать --rotate-search-term и --search-term.',
        )

    if rotate_search_term:
        profile, index = get_rotation_profile(target_date)
        return ResolvedPipelineSearch(
            search_term=profile.search_term,
            category=profile.category,
            rotation_enabled=True,
            rotation_slug=profile.slug,
            rotation_index=index,
        )

    if search_term:
        return ResolvedPipelineSearch(
            search_term=search_term,
            category=category,
        )

    return ResolvedPipelineSearch(
        search_term=category,
        category=category,
    )
