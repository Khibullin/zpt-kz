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

from core.instagram_sanitize import (
    build_instagram_geography_text,
    build_instagram_part_text,
    sanitize_description,
)

if TYPE_CHECKING:
    from core.models import Request

logger = logging.getLogger(__name__)

STORY_WIDTH = 1080
STORY_HEIGHT = 1920
OUTPUT_SUBDIR = 'instagram_stories'

SITE_MARK = 'ZPT.KZ'
SITE_TAGLINE = 'заявки на автозапчасти'
HEADLINE_TEXT = 'Новая заявка'
CTA_LINE_1 = 'Есть эта запчасть?'
CTA_LINE_2 = 'Зарегистрируйтесь на ZPT.KZ'
FOOTER_LINE_1 = 'Контакт покупателя доступен после регистрации'
FOOTER_LINE_2 = 'zpt.kz'

SAFE_ZONE_TOP = 220
SAFE_ZONE_BOTTOM = 1550

PADDING_X = 56
CONTENT_WIDTH = STORY_WIDTH - PADDING_X * 2
LINE_GAP = 12
BLOCK_GAP = 30
LABEL_BODY_GAP = 10
CARD_PAD_X = 36
CARD_PAD_Y = 36
CARD_RADIUS = 28

COLOR_WHITE = (255, 255, 255)
COLOR_BG_TOP = (255, 255, 255)
COLOR_BG_BOTTOM = (252, 248, 248)
COLOR_BRAND = (239, 49, 36)
COLOR_TITLE = (17, 24, 39)
COLOR_BODY = (31, 41, 55)
COLOR_LABEL = (107, 114, 128)
COLOR_FOOTER = (107, 114, 128)
COLOR_BORDER = (254, 226, 226)
COLOR_CARD_OUTLINE = (229, 231, 235)
COLOR_DECO = (243, 244, 246)
COLOR_DECO_SOFT = (254, 242, 242)


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
    image = Image.new('RGB', (STORY_WIDTH, STORY_HEIGHT), COLOR_BG_TOP)
    draw = ImageDraw.Draw(image)
    for y in range(STORY_HEIGHT):
        ratio = y / max(STORY_HEIGHT - 1, 1)
        color = tuple(
            int(COLOR_BG_TOP[index] * (1 - ratio) + COLOR_BG_BOTTOM[index] * ratio)
            for index in range(3)
        )
        draw.line((0, y, STORY_WIDTH, y), fill=color)
    return image


def _draw_decorative_pattern(draw: ImageDraw.ImageDraw) -> None:
    """Лёгкие декоративные элементы вне основной safe-zone."""
    stroke = 2
    draw.ellipse((70, 120, 180, 210), outline=COLOR_DECO, width=stroke)
    draw.ellipse((900, 130, 1010, 210), outline=COLOR_DECO, width=stroke)
    draw.ellipse((80, 1580, 170, 1670), outline=COLOR_DECO_SOFT, width=1)
    draw.ellipse((910, 1580, 1000, 1670), outline=COLOR_DECO_SOFT, width=1)


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

    return ' '.join(parts) if parts else 'Не указано'


def _format_part_line(product_request: Request) -> str:
    return build_instagram_part_text(
        category=product_request.category,
        description=product_request.description,
        article=product_request.article,
    )


def _format_city_line(product_request: Request) -> str:
    return build_instagram_geography_text(
        search_scope=getattr(product_request, 'search_scope', 'city'),
        city=product_request.city,
        selected_cities=product_request.selected_cities,
    )


def build_publication_caption(product_request: Request) -> str:
    """Безопасный текст карточки для хранения и превью в админке."""
    lines = [
        f'АВТО: {_format_vehicle_line(product_request)}',
        f'ДЕТАЛЬ: {_format_part_line(product_request)}',
        f'ГЕОГРАФИЯ: {_format_city_line(product_request)}',
    ]
    return '\n'.join(lines)


def _measure_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    *,
    text: str,
    y: int,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> int:
    width, height = _measure_text(draw, text, font)
    x = (STORY_WIDTH - width) // 2
    draw.text((x, y), text, font=font, fill=fill)
    return y + height


def _draw_centered_lines(
    draw: ImageDraw.ImageDraw,
    *,
    lines: list[str],
    y: int,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    line_gap: int = LINE_GAP,
) -> int:
    cursor_y = y
    for line in lines:
        cursor_y = _draw_centered_text(
            draw,
            text=line,
            y=cursor_y,
            font=font,
            fill=fill,
        ) + line_gap
    return cursor_y - line_gap


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


def _wrap_lines_fitted(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    max_width: int,
    sizes: list[int],
    max_lines: int,
    bold: bool,
) -> tuple[list[str], ImageFont.ImageFont]:
    for size in sizes:
        font = _load_font(size, bold=bold)
        lines = _wrap_paragraph(draw, text, font, max_width, max_lines=max_lines)
        if all(_measure_text(draw, line, font)[0] <= max_width for line in lines):
            return lines, font

    font = _load_font(sizes[-1], bold=bold)
    return _wrap_paragraph(draw, text, font, max_width, max_lines=max_lines), font


def _estimate_centered_block_height(
    draw: ImageDraw.ImageDraw,
    sections: list[tuple[str, list[str], ImageFont.ImageFont, ImageFont.ImageFont]],
) -> int:
    total = 0
    for index, (_label, body_lines, label_font, body_font) in enumerate(sections):
        if index:
            total += BLOCK_GAP
        _, label_height = _measure_text(draw, _label, label_font)
        total += label_height + LABEL_BODY_GAP
        for line in body_lines:
            _, line_height = _measure_text(draw, line, body_font)
            total += line_height + LINE_GAP
        if body_lines:
            total -= LINE_GAP
    return total


def _draw_centered_info_block(
    draw: ImageDraw.ImageDraw,
    *,
    y_start: int,
    sections: list[tuple[str, list[str], ImageFont.ImageFont, ImageFont.ImageFont, tuple[int, int, int]]],
) -> int:
    cursor_y = y_start
    for index, (label, body_lines, label_font, body_font, body_color) in enumerate(sections):
        if index:
            cursor_y += BLOCK_GAP
        cursor_y = _draw_centered_text(
            draw,
            text=label,
            y=cursor_y,
            font=label_font,
            fill=COLOR_LABEL,
        ) + LABEL_BODY_GAP
        cursor_y = _draw_centered_lines(
            draw,
            lines=body_lines,
            y=cursor_y,
            font=body_font,
            fill=body_color,
            line_gap=LINE_GAP,
        )
    return cursor_y


def _draw_header_block(draw: ImageDraw.ImageDraw, *, y_start: int) -> int:
    brand_font = _load_font(52, bold=True)
    tagline_font = _load_font(30, bold=False)
    headline_font = _load_font(56, bold=True)

    cursor_y = y_start
    cursor_y = _draw_centered_text(
        draw,
        text=SITE_MARK,
        y=cursor_y,
        font=brand_font,
        fill=COLOR_BRAND,
    ) + 10
    cursor_y = _draw_centered_text(
        draw,
        text=SITE_TAGLINE,
        y=cursor_y,
        font=tagline_font,
        fill=COLOR_LABEL,
    ) + 24
    cursor_y = _draw_centered_text(
        draw,
        text=HEADLINE_TEXT,
        y=cursor_y,
        font=headline_font,
        fill=COLOR_TITLE,
    )
    return cursor_y


def _draw_cta_card(
    draw: ImageDraw.ImageDraw,
    *,
    y: int,
    primary_font: ImageFont.ImageFont,
    secondary_font: ImageFont.ImageFont,
) -> int:
    vertical_pad = 28
    line_spacing = 14
    line1_height = _measure_text(draw, CTA_LINE_1, primary_font)[1]
    line2_height = _measure_text(draw, CTA_LINE_2, secondary_font)[1]
    card_height = vertical_pad + line1_height + line_spacing + line2_height + vertical_pad

    left = PADDING_X
    right = STORY_WIDTH - PADDING_X
    draw.rounded_rectangle(
        (left, y, right, y + card_height),
        radius=22,
        fill=COLOR_WHITE,
        outline=COLOR_BRAND,
        width=3,
    )

    text_y = y + vertical_pad
    text_y = _draw_centered_text(
        draw,
        text=CTA_LINE_1,
        y=text_y,
        font=primary_font,
        fill=COLOR_BRAND,
    ) + line_spacing
    _draw_centered_text(
        draw,
        text=CTA_LINE_2,
        y=text_y,
        font=secondary_font,
        fill=COLOR_BODY,
    )
    return y + card_height


def _draw_footer(
    draw: ImageDraw.ImageDraw,
    *,
    font: ImageFont.ImageFont,
    small_font: ImageFont.ImageFont,
    y_start: int,
) -> None:
    cursor_y = y_start
    cursor_y = _draw_centered_text(
        draw,
        text=FOOTER_LINE_1,
        y=cursor_y,
        font=font,
        fill=COLOR_FOOTER,
    ) + 10
    _draw_centered_text(
        draw,
        text=FOOTER_LINE_2,
        y=cursor_y,
        font=small_font,
        fill=COLOR_BRAND,
    )


def _build_output_filename(product_request: Request) -> str:
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return f'request_{product_request.access_token}_{timestamp}.jpg'


def generate_instagram_story(product_request: Request) -> tuple[Path, str]:
    """
    Создаёт JPEG-карточку 1080×1920 для Instagram Stories и сохраняет её
    в ``MEDIA_ROOT/instagram_stories/``.

    :returns: (абсолютный путь к файлу, безопасный caption).
    :raises InstagramStoryGenerationError: если сохранить изображение не удалось.
    """
    if product_request is None or product_request.pk is None:
        raise InstagramStoryGenerationError('Для генерации нужна сохранённая заявка.')

    caption = build_publication_caption(product_request)

    try:
        image = _load_background()
        draw = ImageDraw.Draw(image)
        _draw_decorative_pattern(draw)

        inner_width = CONTENT_WIDTH - CARD_PAD_X * 2

        label_font = _load_font(28, bold=True)
        vehicle_lines, vehicle_font = _wrap_lines_fitted(
            draw,
            _format_vehicle_line(product_request),
            max_width=inner_width,
            sizes=[72, 64, 56],
            max_lines=2,
            bold=True,
        )
        part_lines, part_font = _wrap_lines_fitted(
            draw,
            _format_part_line(product_request),
            max_width=inner_width,
            sizes=[60, 54, 48],
            max_lines=3,
            bold=True,
        )
        city_lines, city_font = _wrap_lines_fitted(
            draw,
            _format_city_line(product_request),
            max_width=inner_width,
            sizes=[52, 46, 40],
            max_lines=2,
            bold=True,
        )

        cta_primary_font = _load_font(42, bold=True)
        cta_secondary_font = _load_font(34, bold=False)
        footer_font = _load_font(28, bold=False)
        footer_small_font = _load_font(30, bold=True)

        info_sections = [
            ('АВТО', vehicle_lines, label_font, vehicle_font, COLOR_TITLE),
            ('ДЕТАЛЬ', part_lines, label_font, part_font, COLOR_BODY),
            ('ГЕОГРАФИЯ', city_lines, label_font, city_font, COLOR_BRAND),
        ]

        info_height = _estimate_centered_block_height(
            draw,
            [(label, lines, label_font, body_font) for label, lines, label_font, body_font, _ in info_sections],
        )
        cta_height = (
            28
            + _measure_text(draw, CTA_LINE_1, cta_primary_font)[1]
            + 14
            + _measure_text(draw, CTA_LINE_2, cta_secondary_font)[1]
            + 28
        )
        footer_height = (
            _measure_text(draw, FOOTER_LINE_1, footer_font)[1]
            + 10
            + _measure_text(draw, FOOTER_LINE_2, footer_small_font)[1]
        )

        card_height = info_height + CARD_PAD_Y * 2
        stack_height = card_height + 28 + cta_height + 28 + footer_height
        stack_top = max(SAFE_ZONE_TOP, (SAFE_ZONE_BOTTOM - stack_height) // 2)
        if stack_top + stack_height > SAFE_ZONE_BOTTOM:
            stack_top = max(SAFE_ZONE_TOP, SAFE_ZONE_BOTTOM - stack_height)

        header_bottom = _draw_header_block(draw, y_start=SAFE_ZONE_TOP - 12)

        card_left = PADDING_X
        card_right = STORY_WIDTH - PADDING_X
        card_top = max(header_bottom + 28, stack_top)
        card_bottom = card_top + card_height

        draw.rounded_rectangle(
            (card_left, card_top, card_right, card_bottom),
            radius=CARD_RADIUS,
            fill=COLOR_WHITE,
            outline=COLOR_CARD_OUTLINE,
            width=2,
        )
        draw.rounded_rectangle(
            (card_left + 4, card_top + 4, card_right - 4, card_bottom - 4),
            radius=CARD_RADIUS - 4,
            outline=COLOR_BORDER,
            width=1,
        )

        _draw_centered_info_block(
            draw,
            y_start=card_top + CARD_PAD_Y,
            sections=info_sections,
        )

        cta_bottom = _draw_cta_card(
            draw,
            y=card_bottom + 28,
            primary_font=cta_primary_font,
            secondary_font=cta_secondary_font,
        )

        footer_y = min(cta_bottom + 28, SAFE_ZONE_BOTTOM - footer_height)
        _draw_footer(
            draw,
            font=footer_font,
            small_font=footer_small_font,
            y_start=footer_y,
        )

        output_dir = _output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / _build_output_filename(product_request)
        image = image.convert('RGB')
        image.save(output_path, format='JPEG', quality=92, optimize=True)

        logger.info('Instagram Story сохранена: %s (request_id=%s)', output_path, product_request.pk)
        return output_path.resolve(), caption

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
    """Проверяет, есть ли уже публикация Instagram для заявки."""
    from core.models import InstagramPublication, Request

    if InstagramPublication.objects.filter(request_id=request_id).exists():
        return True

    output_dir = _output_dir()
    if not output_dir.is_dir():
        return False
    try:
        access_token = Request.objects.values_list('access_token', flat=True).get(pk=request_id)
    except Request.DoesNotExist:
        return False
    return any(output_dir.glob(f'request_{access_token}_*.jpg'))


def try_generate_instagram_story(product_request: Request) -> Path | None:
    """
    Устаревшая обёртка: делегирует в ``schedule_instagram_publication_for_request``.
    """
    from catalog.instagram_service import schedule_instagram_publication_for_request

    if product_request is None or product_request.pk is None:
        return None

    schedule_instagram_publication_for_request(product_request.pk)

    from core.models import InstagramPublication

    publication = InstagramPublication.objects.filter(request_id=product_request.pk).first()
    if publication and publication.image:
        return Path(settings.MEDIA_ROOT) / publication.image.name
    return None
