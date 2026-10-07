"""Источники изображений для баннера заявки.

Случайный поиск картинок в интернете не используется.
Если достоверного изображения нет, баннер остаётся с текстом
или с нейтральным силуэтом, который не выдаётся за фото нужной машины.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from PIL import Image, ImageDraw

SOURCE_VERIFIED = 'verified_model'
SOURCE_CACHE = 'approved_cache'
SOURCE_LICENSED = 'licensed_external'
SOURCE_CUSTOMER = 'customer_photo'
SOURCE_GENERIC = 'generic_fallback'
SOURCE_NONE = 'none'

CONFIDENCE_HIGH = 'high'
CONFIDENCE_MEDIUM = 'medium'
CONFIDENCE_LOW = 'low'

# Пустая таблица: поколение не выбирается наугад.
# Ключ: (brand casefold, model casefold) -> ((year_from, year_to, generation), ...)
GENERATION_BOUNDARIES: dict[tuple[str, str], tuple[tuple[int, int, str], ...]] = {}


@dataclass(frozen=True)
class VisualAsset:
    image: Image.Image | None
    source_type: str
    source_reference: str
    brand: str
    model: str
    generation: str
    part: str
    confidence: str
    verified_at: str
    license_status: str
    blocks_publish: bool = False
    review_reason: str = ''

    def provenance(self) -> dict:
        return {
            'source_type': self.source_type,
            'source_reference': self.source_reference,
            'brand': self.brand,
            'model': self.model,
            'generation': self.generation,
            'part': self.part,
            'confidence': self.confidence,
            'verified_at': self.verified_at,
            'license_status': self.license_status,
        }


class VehicleVisualResolver:
    """Ищет достоверное изображение и не подставляет чужой автомобиль."""

    def resolve(
        self,
        *,
        brand: str,
        model: str,
        year: int | None = None,
        boundaries: dict | None = None,
    ) -> VisualAsset:
        generation, blocked, reason = _generation_for(brand, model, year, boundaries)
        if blocked:
            return VisualAsset(
                image=_generic_vehicle_image(),
                source_type=SOURCE_GENERIC,
                source_reference='generation-boundary-fallback',
                brand=brand,
                model=model,
                generation='',
                part='',
                confidence=CONFIDENCE_LOW,
                verified_at='',
                license_status='internal-illustration',
                blocks_publish=True,
                review_reason=reason,
            )

        verified = _local_verified_vehicle(brand, model, generation)
        if verified is not None:
            return verified
        cached = _cached_vehicle(brand, model, generation)
        if cached is not None:
            return cached
        return VisualAsset(
            image=_generic_vehicle_image(),
            source_type=SOURCE_GENERIC,
            source_reference='neutral-silhouette',
            brand=brand,
            model=model,
            generation=generation,
            part='',
            confidence=CONFIDENCE_LOW,
            verified_at='',
            license_status='internal-illustration',
        )


def resolve_customer_part_photos(product_request, *, limit: int = 3) -> list[VisualAsset]:
    """Реальные фото покупателя. Если файла нет, деталь остаётся без картинки."""
    if product_request is None or not getattr(product_request, 'pk', None):
        return []
    assets: list[VisualAsset] = []
    photos = getattr(product_request, 'photos', None)
    if photos is None:
        return []
    for photo in photos.all()[:limit]:
        name = getattr(getattr(photo, 'image', None), 'name', '') or ''
        if not name:
            continue
        path = Path(settings.MEDIA_ROOT) / name
        if not path.is_file():
            continue
        try:
            image = Image.open(path).convert('RGB')
        except OSError:
            continue
        assets.append(
            VisualAsset(
                image=image,
                source_type=SOURCE_CUSTOMER,
                source_reference=name,
                brand=_text(getattr(product_request, 'brand', '')),
                model=_text(getattr(product_request, 'model', '')),
                generation='',
                part='',
                confidence=CONFIDENCE_HIGH,
                verified_at=datetime.now(timezone.utc).isoformat(),
                license_status='buyer-upload',
            )
        )
    return assets


def _generation_for(brand: str, model: str, year: int | None, boundaries: dict | None):
    table = GENERATION_BOUNDARIES if boundaries is None else boundaries
    key = (_text(brand).casefold(), _text(model).casefold())
    ranges = table.get(key) or ()
    if not ranges or not year:
        return '', False, ''
    matches = [generation for start, end, generation in ranges if start <= int(year) <= end]
    if len(matches) == 1:
        return matches[0], False, ''
    from catalog.instagram_public_text import REASON_AMBIGUOUS_GENERATION

    return '', True, REASON_AMBIGUOUS_GENERATION


def _slug(*parts: str) -> str:
    raw = '-'.join(_text(part).casefold() for part in parts if _text(part))
    slug = re.sub(r'[^a-z0-9]+', '-', raw).strip('-')
    return slug or 'vehicle'


def _local_verified_vehicle(brand: str, model: str, generation: str) -> VisualAsset | None:
    filename = f'{_slug(brand, model, generation)}.jpg'
    path = Path(settings.BASE_DIR) / 'static' / 'instagram' / 'vehicles' / filename
    return _asset_from_file(
        path,
        source_type=SOURCE_VERIFIED,
        brand=brand,
        model=model,
        generation=generation,
        confidence=CONFIDENCE_HIGH,
        license_status='project-verified',
    )


def _cached_vehicle(brand: str, model: str, generation: str) -> VisualAsset | None:
    filename = f'{_slug(brand, model, generation)}.jpg'
    path = Path(settings.MEDIA_ROOT) / 'instagram_visual_cache' / 'vehicles' / filename
    return _asset_from_file(
        path,
        source_type=SOURCE_CACHE,
        brand=brand,
        model=model,
        generation=generation,
        confidence=CONFIDENCE_HIGH,
        license_status='approved-cache',
    )


def _asset_from_file(
    path: Path,
    *,
    source_type: str,
    brand: str,
    model: str,
    generation: str,
    confidence: str,
    license_status: str,
) -> VisualAsset | None:
    if not path.is_file():
        return None
    try:
        image = Image.open(path).convert('RGB')
    except OSError:
        return None
    verified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    return VisualAsset(
        image=image,
        source_type=source_type,
        source_reference=path.name,
        brand=brand,
        model=model,
        generation=generation,
        part='',
        confidence=confidence,
        verified_at=verified_at,
        license_status=license_status,
    )


def _generic_vehicle_image() -> Image.Image:
    image = Image.new('RGB', (640, 280), (232, 234, 238))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((70, 120, 570, 210), radius=28, fill=(70, 76, 84))
    draw.ellipse((150, 180, 230, 260), fill=(36, 39, 44))
    draw.ellipse((420, 180, 500, 260), fill=(36, 39, 44))
    draw.polygon([(150, 120), (230, 70), (430, 70), (500, 120)], fill=(90, 98, 108))
    return image


def _text(value) -> str:
    return ' '.join(str(value or '').split())
