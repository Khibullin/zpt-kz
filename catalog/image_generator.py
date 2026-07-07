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

from core.instagram_sanitize import sanitize_description

if TYPE_CHECKING:
    from core.models import Request

logger = logging.getLogger(__name__)

STORY_WIDTH = 1080
STORY_HEIGHT = 1920
OUTPUT_SUBDIR = 'instagram_stories'

FOOTER_TEXT = 'Получите контакт покупателя после регистрации на ZPT.KZ'
CTA_LINE_1 = 'Есть эта запчасть?'
CTA_LINE_2 = 'Зарегистрируйтесь на ZPT.KZ'
SITE_MARK = 'ZPT.KZ'
HEADLINE_TEXT = 'Новая заявка на запчасть'

PADDING_X = 48
CONTENT_WIDTH = STORY_WIDTH - PADDING_X * 2
LINE_GAP = 14
BLOCK_GAP = 38
LABEL_BODY_GAP = 12
CARD_PAD_X = 20
CARD_PAD_Y = 40

COLOR_WHITE = (255, 255, 255)
COLOR_BG = (255, 255, 255)
COLOR_BG_SOFT = (255, 251, 251)
COLOR_BRAND = (239, 61, 47)
COLOR_TITLE = (17, 24, 39)
COLOR_LABEL = (120, 128, 138)
COLOR_BODY = (31, 41, 55)
COLOR_FOOTER = (55, 65, 81)
COLOR_DECO = (220, 224, 230)
COLOR_DECO_SOFT = (236, 239, 244)


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
    image = Image.new('RGB', (STORY_WIDTH, STORY_HEIGHT), COLOR_BG)
    return image


def _draw_decorative_pattern(draw: ImageDraw.ImageDraw) -> None:
    """Лёгкие серые контуры автозапчастей и геометрия в стиле сайта."""
    stroke = 2

    draw.ellipse((70, 260, 210, 400), outline=COLOR_DECO, width=stroke)
    draw.ellipse((110, 300, 170, 360), outline=COLOR_DECO_SOFT, width=stroke)
    draw.line((140, 260, 140, 400), fill=COLOR_DECO_SOFT, width=stroke)

    draw.rounded_rectangle((860, 220, 1010, 320), radius=18, outline=COLOR_DECO, width=stroke)
    draw.line((885, 250, 985, 250), fill=COLOR_DECO_SOFT, width=stroke)
    draw.line((885, 285, 960, 285), fill=COLOR_DECO_SOFT, width=stroke)

    draw.polygon(
        [(120, 1500), (190, 1450), (260, 1500), (260, 1580), (120, 1580)],
        outline=COLOR_DECO,
    )
    draw.ellipse((175, 1520, 205, 1550), outline=COLOR_DECO_SOFT, width=stroke)

    draw.rounded_rectangle((820, 1460, 980, 1560), radius=24, outline=COLOR_DECO, width=stroke)
    draw.ellipse((870, 1495, 930, 1555), outline=COLOR_DECO_SOFT, width=stroke)

    for cx, cy, radius in (
        (180, 620, 28),
        (920, 720, 22),
        (140, 1180, 18),
        (940, 1120, 24),
        (260, 860, 16),
        (800, 980, 20),
    ):
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            outline=COLOR_DECO_SOFT,
            width=1,
        )

    for x1, y1, x2, y2 in (
        (60, 520, 180, 520),
        (900, 560, 1020, 560),
        (80, 1320, 220, 1320),
        (860, 1280, 1000, 1280),
    ):
        draw.line((x1, y1, x2, y2), fill=COLOR_DECO_SOFT, width=1)

    draw.arc((430, 1700, 650, 1820), start=200, end=340, fill=COLOR_DECO, width=stroke)
    draw.arc((470, 1720, 610, 1800), start=200, end=340, fill=COLOR_DECO_SOFT, width=1)


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


def _format_part_line(product_request: Request, *, safe_description: str = '') -> str:
    bits: list[str] = []
    category = _normalize_text(product_request.category)
    description = _normalize_text(safe_description)
    article = _normalize_text(product_request.article)

    if category:
        bits.append(category)
    if description:
        bits.append(description)
    if article:
        bits.append(f'Арт. {article}')

    return ' — '.join(bits) if bits else 'Не указано'


def build_publication_caption(product_request: Request) -> str:
    """Безопасный текст карточки для хранения и превью в админке."""
    safe_description = sanitize_description(product_request.description)
    lines = [
        f'АВТО: {_format_vehicle_line(product_request)}',
        f'ДЕТАЛЬ: {_format_part_line(product_request, safe_description=safe_description)}',
        f'ГОРОД: {_format_city_line(product_request)}',
    ]
    return '\n'.join(lines)


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


def _estimate_centered_block_height(
    draw: ImageDraw.ImageDraw,
    sections: list[tuple[str, list[str], ImageFont.ImageFont, ImageFont.ImageFont]],
    *,
    max_width: int,
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
    max_width: int,
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


def _draw_cta_banner(
    draw: ImageDraw.ImageDraw,
    *,
    y: int,
    primary_font: ImageFont.ImageFont,
    secondary_font: ImageFont.ImageFont,
) -> int:
    vertical_pad = 48
    line_spacing = 22
    line1_height = _measure_text(draw, CTA_LINE_1, primary_font)[1]
    line2_height = _measure_text(draw, CTA_LINE_2, secondary_font)[1]
    banner_height = vertical_pad + line1_height + line_spacing + line2_height + vertical_pad
    draw.rounded_rectangle(
        (PADDING_X - 4, y, STORY_WIDTH - PADDING_X + 4, y + banner_height),
        radius=34,
        fill=COLOR_BRAND,
    )
    text_y = y + vertical_pad
    text_y = _draw_centered_text(
        draw,
        text=CTA_LINE_1,
        y=text_y,
        font=primary_font,
        fill=COLOR_WHITE,
    ) + line_spacing
    _draw_centered_text(
        draw,
        text=CTA_LINE_2,
        y=text_y,
        font=secondary_font,
        fill=COLOR_WHITE,
    )
    return y + banner_height


def _draw_footer(
    draw: ImageDraw.ImageDraw,
    *,
    font: ImageFont.ImageFont,
    max_width: int,
    cta_bottom: int,
) -> None:
    lines = _wrap_paragraph(draw, FOOTER_TEXT, font, max_width, max_lines=3)
    total_height = sum(_measure_text(draw, line, font)[1] + LINE_GAP for line in lines) - LINE_GAP
    footer_y = max(cta_bottom + 42, STORY_HEIGHT - 150 - total_height)
    for line in lines:
        _draw_centered_text(draw, text=line, y=footer_y, font=font, fill=COLOR_FOOTER)
        footer_y += _measure_text(draw, line, font)[1] + LINE_GAP


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


def _build_output_filename(product_request: Request) -> str:
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return f'request_{product_request.pk}_{timestamp}.png'


def generate_instagram_story(product_request: Request) -> tuple[Path, str]:
    """
    Создаёт PNG-карточку 1080×1920 для Instagram Stories и сохраняет её
    в ``MEDIA_ROOT/instagram_stories/``.

    :returns: (абсолютный путь к файлу, безопасный caption).
    :raises InstagramStoryGenerationError: если сохранить изображение не удалось.
    """
    if product_request is None or product_request.pk is None:
        raise InstagramStoryGenerationError('Для генерации нужна сохранённая заявка.')

    safe_description = sanitize_description(product_request.description)
    caption = build_publication_caption(product_request)

    try:
        image = _load_background()
        draw = ImageDraw.Draw(image)

        _draw_decorative_pattern(draw)

        site_font = _load_font(44, bold=True)
        headline_font = _load_font(64, bold=True)
        label_font = _load_font(42, bold=True)
        vehicle_font = _load_font(106, bold=True)
        part_font = _load_font(82, bold=True)
        city_font = _load_font(96, bold=True)
        cta_primary_font = _load_font(72, bold=True)
        cta_secondary_font = _load_font(58, bold=True)
        footer_font = _load_font(48, bold=False)

        draw.rectangle((0, 0, STORY_WIDTH, 24), fill=COLOR_BRAND)
        draw.rectangle((0, STORY_HEIGHT - 24, STORY_WIDTH, STORY_HEIGHT), fill=COLOR_BRAND)

        cursor_y = 48
        cursor_y = _draw_centered_text(
            draw,
            text=SITE_MARK,
            y=cursor_y,
            font=site_font,
            fill=COLOR_BRAND,
        ) + 18

        for line in _wrap_paragraph(draw, HEADLINE_TEXT, headline_font, CONTENT_WIDTH, max_lines=2):
            cursor_y = _draw_centered_text(
                draw,
                text=line,
                y=cursor_y,
                font=headline_font,
                fill=COLOR_TITLE,
            ) + 10

        vehicle_lines = _wrap_paragraph(
            draw,
            _format_vehicle_line(product_request),
            vehicle_font,
            CONTENT_WIDTH - CARD_PAD_X * 2,
            max_lines=2,
        )
        part_lines = _wrap_paragraph(
            draw,
            _format_part_line(product_request, safe_description=safe_description),
            part_font,
            CONTENT_WIDTH - CARD_PAD_X * 2,
            max_lines=4,
        )
        city_lines = _wrap_paragraph(
            draw,
            _format_city_line(product_request),
            city_font,
            CONTENT_WIDTH - CARD_PAD_X * 2,
            max_lines=2,
        )

        info_sections = [
            ('АВТО', vehicle_lines, label_font, vehicle_font, COLOR_TITLE),
            ('ДЕТАЛЬ', part_lines, label_font, part_font, COLOR_BODY),
            ('ГОРОД', city_lines, label_font, city_font, COLOR_BRAND),
        ]

        info_height = _estimate_centered_block_height(
            draw,
            [(label, lines, label_font, body_font) for label, lines, label_font, body_font, _ in info_sections],
            max_width=CONTENT_WIDTH,
        )
        cta_height = (
            48
            + _measure_text(draw, CTA_LINE_1, cta_primary_font)[1]
            + 22
            + _measure_text(draw, CTA_LINE_2, cta_secondary_font)[1]
            + 48
        )

        card_height = info_height + CARD_PAD_Y * 2
        stack_height = card_height + 40 + cta_height
        anchor_y = int(STORY_HEIGHT * 0.40)
        stack_top = max(cursor_y + 36, anchor_y - stack_height // 2)
        info_y = stack_top + CARD_PAD_Y

        card_left = PADDING_X - CARD_PAD_X
        card_right = STORY_WIDTH - PADDING_X + CARD_PAD_X
        card_top = stack_top
        card_bottom = stack_top + card_height

        draw.rounded_rectangle(
            (card_left, card_top, card_right, card_bottom),
            radius=40,
            fill=COLOR_WHITE,
            outline=(255, 214, 214),
            width=4,
        )
        draw.rounded_rectangle(
            (card_left + 6, card_top + 6, card_right - 6, card_bottom - 6),
            radius=34,
            outline=COLOR_DECO_SOFT,
            width=2,
        )

        content_bottom = _draw_centered_info_block(
            draw,
            y_start=info_y,
            sections=info_sections,
            max_width=CONTENT_WIDTH - CARD_PAD_X * 2,
        )

        cta_bottom = _draw_cta_banner(
            draw,
            y=card_bottom + 40,
            primary_font=cta_primary_font,
            secondary_font=cta_secondary_font,
        )

        _draw_footer(
            draw,
            font=footer_font,
            max_width=CONTENT_WIDTH,
            cta_bottom=cta_bottom,
        )

        output_dir = _output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / _build_output_filename(product_request)
        image.save(output_path, format='PNG', optimize=True)

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
    from core.models import InstagramPublication

    if InstagramPublication.objects.filter(request_id=request_id).exists():
        return True

    output_dir = _output_dir()
    if not output_dir.is_dir():
        return False
    return any(output_dir.glob(f'request_{request_id}_*.png'))


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
