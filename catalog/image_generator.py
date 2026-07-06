"""
Генератор брендированных карточек Instagram Stories для заявок на запчасти.

На изображение попадают только технические данные заявки — без имени и телефона
клиента (требование Закона РК «О персональных данных»).
"""

from __future__ import annotations

import logging
import textwrap
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    from core.models import Request

logger = logging.getLogger(__name__)

STORY_WIDTH = 1080
STORY_HEIGHT = 1920
OUTPUT_SUBDIR = 'instagram_stories'

TITLE_TEXT = 'Новая заявка на ZPT.kz!'
FOOTER_TEXT = 'Продавцы, пишите клиенту в WhatsApp через сайт ZPT.kz'

PADDING_X = 80
PADDING_TOP = 140
PADDING_BOTTOM = 160
CONTENT_WIDTH = STORY_WIDTH - PADDING_X * 2
LINE_GAP = 12
SECTION_GAP = 44

COLOR_BG_FALLBACK = (245, 246, 248)
COLOR_BRAND = (255, 59, 48)
COLOR_TITLE = (17, 24, 39)
COLOR_LABEL = (107, 114, 128)
COLOR_BODY = (31, 41, 55)
COLOR_FOOTER = (75, 85, 99)
COLOR_ACCENT_BAR = (255, 59, 48)


class InstagramStoryGenerationError(Exception):
    """Ошибка при создании карточки Instagram Story."""


def _static_images_dir() -> Path:
    return Path(settings.BASE_DIR) / 'static' / 'images'


def _output_dir() -> Path:
    return Path(settings.MEDIA_ROOT) / OUTPUT_SUBDIR


def _background_path() -> Path:
    return _static_images_dir() / 'instagram_bg.png'


def _font_candidates(*, bold: bool) -> list[Path]:
    names = (
        ['Inter-Bold.ttf', 'DejaVuSans-Bold.ttf', 'Arial Bold.ttf', 'arialbd.ttf']
        if bold
        else ['Inter-Regular.ttf', 'DejaVuSans.ttf', 'Arial.ttf', 'arial.ttf']
    )
    candidates: list[Path] = [Path(settings.BASE_DIR) / 'static' / 'fonts' / name for name in names]
    candidates.extend(
        Path(path)
        for path in (
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
            if bold
            else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf'
            if bold
            else '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
            'C:/Windows/Fonts/arialbd.ttf' if bold else 'C:/Windows/Fonts/arial.ttf',
            'C:/Windows/Fonts/segoeuib.ttf' if bold else 'C:/Windows/Fonts/segoeui.ttf',
        )
    )
    return candidates


def _load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in _font_candidates(bold=bold):
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                logger.debug('Не удалось загрузить шрифт: %s', candidate)
                continue

    logger.warning(
        'Системный шрифт не найден, используется шрифт Pillow по умолчанию (размер %s).',
        size,
    )
    return ImageFont.load_default()


def _create_fallback_background() -> Image.Image:
    image = Image.new('RGB', (STORY_WIDTH, STORY_HEIGHT), COLOR_BG_FALLBACK)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, STORY_WIDTH, 18), fill=COLOR_ACCENT_BAR)
    draw.rectangle((0, STORY_HEIGHT - 18, STORY_WIDTH, STORY_HEIGHT), fill=COLOR_ACCENT_BAR)
    return image


def _load_background() -> Image.Image:
    bg_path = _background_path()
    if bg_path.is_file():
        try:
            with Image.open(bg_path) as source:
                image = source.convert('RGB')
                return _fit_background(image, (STORY_WIDTH, STORY_HEIGHT))
        except OSError as exc:
            logger.warning('Фон %s не прочитан (%s), используется заглушка.', bg_path, exc)

    return _create_fallback_background()


def _fit_background(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Масштабирует изображение с обрезкой по центру под точный размер Stories."""
    target_w, target_h = size
    src_w, src_h = image.size
    scale = max(target_w / src_w, target_h / src_h)
    resized = image.resize(
        (int(src_w * scale), int(src_h * scale)),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - target_w) // 2
    top = (resized.height - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def _normalize_text(value: str | None) -> str:
    if not value:
        return ''
    return ' '.join(str(value).split())


def _format_vehicle_line(product_request: Request) -> str:
    parts: list[str] = []
    brand = _normalize_text(product_request.brand)
    model = _normalize_text(product_request.model)
    if brand:
        parts.append(brand)
    if model:
        parts.append(model)

    year = getattr(product_request, 'year', None) or getattr(product_request, 'vehicle_year', None)
    year_text = _normalize_text(str(year)) if year else ''
    if year_text:
        parts.append(year_text)

    return ' · '.join(parts) if parts else 'Не указано'


def _format_part_line(product_request: Request) -> str:
    bits: list[str] = []
    category = _normalize_text(product_request.category)
    description = _normalize_text(product_request.description)
    article = _normalize_text(product_request.article)

    if category:
        bits.append(category)
    if description:
        bits.append(description)
    if article:
        bits.append(f'Арт. {article}')

    return ' — '.join(bits) if bits else 'Не указано'


def _format_city_line(product_request: Request) -> str:
    scope = getattr(product_request, 'search_scope', 'city')
    if scope == 'kazakhstan':
        return 'Весь Казахстан'

    if scope == 'custom':
        selected = _normalize_text(product_request.selected_cities)
        if selected:
            return selected

    city = _normalize_text(product_request.city)
    return city or 'Не указан'


def _measure_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _wrap_paragraph(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    *,
    max_lines: int = 8,
) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return ['Не указано']

    words = normalized.split(' ')
    lines: list[str] = []
    current: list[str] = []

    for word in words:
        candidate = ' '.join(current + [word]) if current else word
        width, _ = _measure_text(draw, candidate, font)

        if width <= max_width:
            current.append(word)
            continue

        if current:
            lines.append(' '.join(current))
            current = [word]
        else:
            wrapped = textwrap.wrap(
                word,
                width=max(8, max_width // max(font.size // 2, 1)),
                break_long_words=True,
                break_on_hyphens=False,
            )
            lines.extend(wrapped[:-1])
            current = [wrapped[-1]] if wrapped else []

        if len(lines) >= max_lines:
            if lines:
                lines[-1] = lines[-1].rstrip('.,; ') + '\u2026'
            current = []
            break

    if current and len(lines) < max_lines:
        lines.append(' '.join(current))

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip('.,; ') + '\u2026'

    return lines or ['Не указано']


def _draw_text_block(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    label: str,
    body: str,
    label_font: ImageFont.ImageFont,
    body_font: ImageFont.ImageFont,
    max_width: int,
) -> int:
    draw.text((x, y), label, font=label_font, fill=COLOR_LABEL)
    _, label_height = _measure_text(draw, label, label_font)
    cursor_y = y + label_height + 10

    for line in _wrap_paragraph(draw, body, body_font, max_width):
        draw.text((x, cursor_y), line, font=body_font, fill=COLOR_BODY)
        _, line_height = _measure_text(draw, line, body_font)
        cursor_y += line_height + LINE_GAP

    return cursor_y + SECTION_GAP - LINE_GAP


def _draw_footer(
    draw: ImageDraw.ImageDraw,
    *,
    font: ImageFont.ImageFont,
    max_width: int,
) -> None:
    footer_y = STORY_HEIGHT - PADDING_BOTTOM
    for line in reversed(_wrap_paragraph(draw, FOOTER_TEXT, font, max_width, max_lines=3)):
        _, line_height = _measure_text(draw, line, font)
        footer_y -= line_height
        draw.text((PADDING_X, footer_y), line, font=font, fill=COLOR_FOOTER)
        footer_y -= LINE_GAP


def _build_output_filename(product_request: Request) -> str:
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return f'request_{product_request.pk}_{timestamp}.png'


def generate_instagram_story(product_request: Request) -> Path:
    """
    Создаёт PNG-карточку 1080×1920 для Instagram Stories и сохраняет её
    в ``MEDIA_ROOT/instagram_stories/``.

    :param product_request: заявка ``core.models.Request`` (без персональных данных на картинке).
    :returns: абсолютный путь к сохранённому файлу.
    :raises InstagramStoryGenerationError: если сохранить изображение не удалось.
    """
    if product_request is None or product_request.pk is None:
        raise InstagramStoryGenerationError('Для генерации нужна сохранённая заявка.')

    try:
        image = _load_background()
        draw = ImageDraw.Draw(image)

        title_font = _load_font(64, bold=True)
        label_font = _load_font(30, bold=True)
        body_font = _load_font(42, bold=False)
        footer_font = _load_font(30, bold=False)

        cursor_y = PADDING_TOP

        for line in _wrap_paragraph(draw, TITLE_TEXT, title_font, CONTENT_WIDTH, max_lines=2):
            draw.text((PADDING_X, cursor_y), line, font=title_font, fill=COLOR_BRAND)
            _, line_height = _measure_text(draw, line, title_font)
            cursor_y += line_height + LINE_GAP

        cursor_y += 28
        draw.line(
            (PADDING_X, cursor_y, STORY_WIDTH - PADDING_X, cursor_y),
            fill=(229, 231, 235),
            width=3,
        )
        cursor_y += 36

        cursor_y = _draw_text_block(
            draw,
            x=PADDING_X,
            y=cursor_y,
            label='АВТО',
            body=_format_vehicle_line(product_request),
            label_font=label_font,
            body_font=body_font,
            max_width=CONTENT_WIDTH,
        )
        cursor_y = _draw_text_block(
            draw,
            x=PADDING_X,
            y=cursor_y,
            label='ДЕТАЛЬ',
            body=_format_part_line(product_request),
            label_font=label_font,
            body_font=body_font,
            max_width=CONTENT_WIDTH,
        )
        _draw_text_block(
            draw,
            x=PADDING_X,
            y=cursor_y,
            label='ГОРОД',
            body=_format_city_line(product_request),
            label_font=label_font,
            body_font=body_font,
            max_width=CONTENT_WIDTH,
        )

        _draw_footer(draw, font=footer_font, max_width=CONTENT_WIDTH)

        output_dir = _output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / _build_output_filename(product_request)
        image.save(output_path, format='PNG', optimize=True)

        logger.info('Instagram Story сохранена: %s (request_id=%s)', output_path, product_request.pk)
        return output_path.resolve()

    except InstagramStoryGenerationError:
        raise
    except Exception as exc:
        logger.exception(
            'Ошибка генерации Instagram Story для заявки %s',
            getattr(product_request, 'pk', '?'),
        )
        raise InstagramStoryGenerationError('Не удалось сгенерировать изображение.') from exc


ACTIVE_REQUEST_STATUSES = ('new', 'sent')


def instagram_story_exists(request_id: int) -> bool:
    """Проверяет, есть ли уже сгенерированная карточка для заявки."""
    output_dir = _output_dir()
    if not output_dir.is_dir():
        return False
    return any(output_dir.glob(f'request_{request_id}_*.png'))


def try_generate_instagram_story(product_request: Request) -> Path | None:
    """
    Безопасно генерирует Instagram Story для заявки.

    Ошибки генерации логируются и не пробрасываются наружу.
    После успешной генерации файла пытается опубликовать Story в Instagram.
    """
    try:
        output_path = generate_instagram_story(product_request)
    except InstagramStoryGenerationError as exc:
        logger.warning(
            'Instagram Story не создана для заявки #%s: %s',
            getattr(product_request, 'pk', '?'),
            exc,
        )
        return None

    try:
        from catalog.instagram_api import (
            absolute_media_path_to_relative,
            try_publish_story_to_instagram,
        )

        relative_path = absolute_media_path_to_relative(output_path)
        try_publish_story_to_instagram(relative_path)
    except Exception:
        logger.exception(
            'Instagram publish завершился с ошибкой для заявки #%s',
            getattr(product_request, 'pk', '?'),
        )

    return output_path
