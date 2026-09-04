"""Limits and WebP optimization for seller local image uploads."""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_IMAGE_DIMENSION = 1600
MAX_IMAGE_PIXELS = 40_000_000
WEBP_QUALITY = 85
ALLOWED_IMAGE_FORMATS = frozenset({
    'JPEG',
    'PNG',
    'WEBP',
})
IMAGE_UPLOAD_HELP_TEXT = (
    'JPEG, PNG или WebP, до 5 МБ. Большие изображения '
    'автоматически уменьшаются до 1600×1600.'
)

ERROR_TOO_LARGE = 'Файл больше 5 МБ. Выберите изображение меньшего размера.'
ERROR_FORMAT = 'Допустимы только JPEG, PNG и WebP.'
ERROR_CORRUPT = 'Файл не является корректным JPEG, PNG или WebP изображением.'
ERROR_PIXELS = 'Слишком большое разрешение изображения.'

_STEM_SAFE_RE = re.compile(r'[^A-Za-z0-9_-]+')


def _safe_stem(name: str) -> str:
    stem = Path(str(name or '')).stem
    cleaned = _STEM_SAFE_RE.sub('-', stem).strip('-_')[:80].strip('-_')
    return cleaned or 'image'


def _has_transparency(image: Image.Image) -> bool:
    if image.mode in {'RGBA', 'LA', 'PA'}:
        return True
    if image.mode == 'P' and 'transparency' in image.info:
        return True
    return 'transparency' in image.info


def optimize_uploaded_image(uploaded):
    if not uploaded:
        return uploaded
    if not isinstance(uploaded, UploadedFile):
        return uploaded

    size = getattr(uploaded, 'size', None)
    if size is not None and size > MAX_UPLOAD_BYTES:
        raise ValidationError(ERROR_TOO_LARGE)

    try:
        uploaded.seek(0)
        data = uploaded.read(MAX_UPLOAD_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise ValidationError(ERROR_CORRUPT) from exc

    if len(data) > MAX_UPLOAD_BYTES:
        raise ValidationError(ERROR_TOO_LARGE)

    previous_max_pixels = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        try:
            with Image.open(BytesIO(data)) as probe:
                fmt = (probe.format or '').upper()
                if fmt not in ALLOWED_IMAGE_FORMATS:
                    raise ValidationError(ERROR_FORMAT)
                width, height = probe.size
                if width * height > MAX_IMAGE_PIXELS:
                    raise ValidationError(ERROR_PIXELS)
                probe.verify()

            with Image.open(BytesIO(data)) as image:
                image.load()
                transposed = ImageOps.exif_transpose(image)
                if transposed is not None:
                    image = transposed
                image.thumbnail(
                    (MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION),
                    Image.Resampling.LANCZOS,
                )
                if _has_transparency(image):
                    image = image.convert('RGBA')
                else:
                    image = image.convert('RGB')
                output = BytesIO()
                image.save(
                    output,
                    format='WEBP',
                    quality=WEBP_QUALITY,
                    method=6,
                )
        except ValidationError:
            raise
        except Image.DecompressionBombError as exc:
            raise ValidationError(ERROR_PIXELS) from exc
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            SyntaxError,
        ) as exc:
            raise ValidationError(ERROR_CORRUPT) from exc
    finally:
        Image.MAX_IMAGE_PIXELS = previous_max_pixels

    return ContentFile(
        output.getvalue(),
        name=f'{_safe_stem(getattr(uploaded, "name", "") or "")}.webp',
    )
