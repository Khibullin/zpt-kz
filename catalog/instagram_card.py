"""Программная карточка заявки для ленты и сторис Instagram.

Рисунок собирается из данных заявки. Генеративная модель не используется.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

from catalog.instagram_copy import (
    BADGE,
    BRAND,
    BUYER_BODY,
    BUYER_NOTE,
    BUYER_TITLE,
    FOOTER,
    HEADLINE,
    SELLER_BODY,
    SELLER_NOTE,
    SELLER_TITLE,
    TAGLINE_LINES,
)

logger = logging.getLogger(__name__)

FEED_SIZE = (1080, 1350)
STORY_SIZE = (1080, 1920)

COLOR_WHITE = (255, 255, 255)
COLOR_RED = (227, 30, 36)
COLOR_INK = (23, 28, 36)
COLOR_BODY = (55, 61, 70)
COLOR_MUTED = (90, 96, 106)
COLOR_FOOTER = (107, 114, 128)
COLOR_CARD = (246, 247, 249)
COLOR_CARD_LINE = (228, 230, 234)
COLOR_PINK = (255, 242, 242)
COLOR_DIVIDER = (214, 216, 220)

_SCALE_STEPS = (1.0, 0.94, 0.88, 0.82, 0.76, 0.70, 0.64, 0.58)


@dataclass(frozen=True)
class InstagramCardContent:
    vehicle: str = ''
    part: str = ''
    location: str = ''
    request_number: str = ''


@dataclass(frozen=True)
class CardRender:
    image: Image.Image
    headline_size: int
    vehicle_size: int
    part_size: int


@dataclass(frozen=True)
class _Canvas:
    width: int
    height: int
    pad_x: int
    safe_top: int
    safe_bottom: int
    story: bool


def _font_candidates(*, bold: bool) -> list[Path]:
    names = (
        ['NotoSans-Bold.ttf', 'Inter-Bold.ttf', 'DejaVuSans-Bold.ttf']
        if bold
        else ['NotoSans-Regular.ttf', 'Inter-Regular.ttf', 'DejaVuSans.ttf']
    )
    candidates = [Path(settings.BASE_DIR) / 'static' / 'fonts' / name for name in names]
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
    size = max(12, int(size))
    for candidate in _font_candidates(bold=bold):
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                logger.debug('Не удалось загрузить шрифт: %s', candidate)
                continue
    logger.warning('Шрифт с кириллицей не найден, используется шрифт Pillow по умолчанию.')
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    normalized = ' '.join(str(text or '').split())
    if not normalized:
        return []
    lines: list[str] = []
    current = ''
    for word in normalized.split(' '):
        candidate = word if not current else f'{current} {word}'
        if _text_size(draw, candidate, font)[0] <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ''
        if _text_size(draw, word, font)[0] <= max_width:
            current = word
            continue
        chunk = ''
        for char in word:
            trial = chunk + char
            if _text_size(draw, trial, font)[0] <= max_width:
                chunk = trial
            else:
                if chunk:
                    lines.append(chunk)
                chunk = char
        current = chunk
    if current:
        lines.append(current)
    return lines


def _fit_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    max_width: int,
    sizes: list[int],
    max_lines: int,
    bold: bool,
) -> tuple[list[str], ImageFont.ImageFont, int]:
    cleaned = ' '.join(str(text or '').split())
    if not cleaned:
        font = _load_font(sizes[-1], bold=bold)
        return [], font, sizes[-1]
    for size in sizes:
        font = _load_font(size, bold=bold)
        lines = _wrap(draw, cleaned, font, max_width)
        if lines and len(lines) <= max_lines and all(
            _text_size(draw, line, font)[0] <= max_width for line in lines
        ):
            return lines, font, size
    font = _load_font(sizes[-1], bold=bold)
    lines = _wrap(draw, cleaned, font, max_width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(' .,;') + '…'
    return lines, font, sizes[-1]


def _line_height(draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont) -> int:
    return _text_size(draw, 'Аy', font)[1]


def _block_height(draw: ImageDraw.ImageDraw, lines: list[str], font: ImageFont.ImageFont, gap: int) -> int:
    if not lines:
        return 0
    height = _line_height(draw, font)
    return len(lines) * height + (len(lines) - 1) * gap


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    *,
    x: int,
    y: int,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    gap: int,
    align: str = 'left',
    width: int = 0,
) -> int:
    cursor = y
    line_height = _line_height(draw, font)
    for line in lines:
        line_x = x
        if align == 'center' and width:
            line_x = x + max(0, (width - _text_size(draw, line, font)[0]) // 2)
        draw.text((line_x, cursor), line, font=font, fill=fill)
        cursor += line_height + gap
    return cursor - gap if lines else y


def _canvas_for(kind: str) -> _Canvas:
    if kind == 'story':
        # Верх и низ закрывает интерфейс сторис. Бренд остаётся ниже этой зоны.
        return _Canvas(1080, 1920, 64, 250, 1920 - 250, True)
    # Пост 4:5 в сетке профиля обрезается до центрального квадрата.
    # Бренд и заголовок должны попадать в него, а не в срезаемые поля.
    crop = (1350 - 1080) // 2
    inset = 32
    return _Canvas(1080, 1350, 48, crop + inset, 1350 - crop - inset, False)


def _sizes(scale: float, *, story: bool) -> dict[str, int]:
    base = {
        'brand': 50 if story else 44,
        'tagline': 24 if story else 22,
        'headline': 40 if story else 34,
        'badge': 18 if story else 16,
        'number': 22 if story else 20,
        'vehicle': 72 if story else 62,
        'part': 64 if story else 56,
        'location': 28 if story else 24,
        'seller_title': 42 if story else 36,
        'seller_body': 28 if story else 25,
        'seller_note': 28 if story else 25,
        'buyer_title': 38 if story else 34,
        'buyer_body': 28 if story else 25,
        'buyer_note': 30 if story else 26,
        'footer': 24 if story else 20,
    }
    return {key: max(14, int(value * scale)) for key, value in base.items()}


def _size_steps(preferred: int, floor: int) -> list[int]:
    steps = []
    size = preferred
    while size >= floor:
        steps.append(size)
        size -= 2
    if not steps:
        steps.append(floor)
    return steps


def render_instagram_card(content: InstagramCardContent, *, kind: str) -> CardRender:
    """Рисует карточку 1080×1350 (лента) или 1080×1920 (сторис)."""
    if kind not in ('feed', 'story'):
        raise ValueError(f'Неизвестный формат карточки: {kind}')
    canvas = _canvas_for(kind)
    probe = ImageDraw.Draw(Image.new('RGB', (canvas.width, canvas.height), COLOR_WHITE))
    chosen = None
    for scale in _SCALE_STEPS:
        layout = _measure(probe, content, canvas, scale)
        if layout['bottom_gap'] >= 0:
            chosen = layout
            break
    if chosen is None:
        chosen = _measure(probe, content, canvas, _SCALE_STEPS[-1])
        if chosen['bottom_gap'] < 0:
            chosen = _compress_to_safe_area(chosen, canvas)

    image = Image.new('RGB', (canvas.width, canvas.height), COLOR_WHITE)
    draw = ImageDraw.Draw(image)
    _paint(draw, content, canvas, chosen)
    return CardRender(
        image=image,
        headline_size=chosen['headline_size'],
        vehicle_size=chosen['vehicle_size'],
        part_size=chosen['part_size'],
    )


def _measure(draw: ImageDraw.ImageDraw, content: InstagramCardContent, canvas: _Canvas, scale: float) -> dict:
    sizes = _sizes(scale, story=canvas.story)
    inner = canvas.width - canvas.pad_x * 2
    card_pad = 34 if canvas.story else 30
    text_width = inner - card_pad * 2
    gap = 8

    vehicle_lines, vehicle_font, vehicle_size = _fit_lines(
        draw,
        content.vehicle,
        max_width=text_width,
        sizes=_size_steps(sizes['vehicle'], 34),
        max_lines=4,
        bold=True,
    )
    part_lines, part_font, part_size = _fit_lines(
        draw,
        content.part,
        max_width=text_width,
        sizes=_size_steps(sizes['part'], 32),
        max_lines=6,
        bold=True,
    )
    hero = min(
        [size for size, lines in ((vehicle_size, vehicle_lines), (part_size, part_lines)) if lines] or [sizes['headline'] + 8]
    )
    headline_size = min(sizes['headline'], hero - 6)
    headline_size = max(22, headline_size)
    headline_font = _load_font(headline_size, bold=True)
    headline_lines = _wrap(draw, HEADLINE, headline_font, inner)
    location_font = _load_font(sizes['location'], bold=False)
    location_lines = _wrap(draw, content.location, location_font, text_width - 36) if content.location else []

    brand_font = _load_font(sizes['brand'], bold=True)
    tag_font = _load_font(sizes['tagline'], bold=False)
    brand_h = _line_height(draw, brand_font)
    tag_h = _block_height(draw, list(TAGLINE_LINES), tag_font, 2)
    brand_row = max(brand_h, tag_h)

    badge_font = _load_font(sizes['badge'], bold=True)
    number_font = _load_font(sizes['number'], bold=True)
    badge_h = _line_height(draw, badge_font) + 16

    seller_title_font = _load_font(sizes['seller_title'], bold=True)
    seller_body_font = _load_font(sizes['seller_body'], bold=False)
    seller_note_font = _load_font(sizes['seller_note'], bold=True)
    buyer_title_font = _load_font(sizes['buyer_title'], bold=True)
    buyer_body_font = _load_font(sizes['buyer_body'], bold=False)
    buyer_note_font = _load_font(sizes['buyer_note'], bold=True)
    footer_font = _load_font(sizes['footer'], bold=False)

    block_text_width = inner - 56
    seller_title = _wrap(draw, SELLER_TITLE, seller_title_font, block_text_width)
    seller_body = _wrap(draw, SELLER_BODY, seller_body_font, block_text_width)
    seller_note = _wrap(draw, SELLER_NOTE, seller_note_font, block_text_width)
    buyer_title = _wrap(draw, BUYER_TITLE, buyer_title_font, block_text_width)
    buyer_body = _wrap(draw, BUYER_BODY, buyer_body_font, block_text_width)
    buyer_note = _wrap(draw, BUYER_NOTE, buyer_note_font, block_text_width)
    footer_lines = _wrap(draw, FOOTER, footer_font, inner)

    card_inner = 0
    card_inner += badge_h
    card_inner += 18
    if vehicle_lines:
        card_inner += _block_height(draw, vehicle_lines, vehicle_font, gap) + 12
    if part_lines:
        card_inner += _block_height(draw, part_lines, part_font, gap) + 14
    if location_lines:
        card_inner += _block_height(draw, location_lines, location_font, 4) + 4
    card_h = card_inner + card_pad * 2

    def cta_height(title, body, note, title_font, body_font, note_font) -> int:
        height = 28
        height += _block_height(draw, title, title_font, 4) + 8
        height += _block_height(draw, body, body_font, 4) + 10
        height += _block_height(draw, note, note_font, 4)
        height += 28
        return height

    seller_h = cta_height(
        seller_title, seller_body, seller_note, seller_title_font, seller_body_font, seller_note_font,
    )
    buyer_h = cta_height(
        buyer_title, buyer_body, buyer_note, buyer_title_font, buyer_body_font, buyer_note_font,
    )
    footer_h = _block_height(draw, footer_lines, footer_font, 4)
    section_gap = 22 if canvas.story else 16
    stack = (
        brand_row
        + 18
        + _block_height(draw, headline_lines, headline_font, 4)
        + section_gap
        + card_h
        + section_gap
        + seller_h
        + section_gap
        + buyer_h
        + 18
        + footer_h
    )
    available = canvas.safe_bottom - canvas.safe_top
    return {
        'scale': scale,
        'sizes': sizes,
        'inner': inner,
        'card_pad': card_pad,
        'text_width': text_width,
        'gap': gap,
        'section_gap': section_gap,
        'brand_font': brand_font,
        'tag_font': tag_font,
        'brand_row': brand_row,
        'headline_lines': headline_lines,
        'headline_font': headline_font,
        'headline_size': headline_size,
        'badge_font': badge_font,
        'badge_h': badge_h,
        'number_font': number_font,
        'vehicle_lines': vehicle_lines,
        'vehicle_font': vehicle_font,
        'vehicle_size': vehicle_size if vehicle_lines else headline_size + 8,
        'part_lines': part_lines,
        'part_font': part_font,
        'part_size': part_size if part_lines else headline_size + 8,
        'location_lines': location_lines,
        'location_font': location_font,
        'card_h': card_h,
        'seller_title': seller_title,
        'seller_body': seller_body,
        'seller_note': seller_note,
        'seller_title_font': seller_title_font,
        'seller_body_font': seller_body_font,
        'seller_note_font': seller_note_font,
        'seller_h': seller_h,
        'buyer_title': buyer_title,
        'buyer_body': buyer_body,
        'buyer_note': buyer_note,
        'buyer_title_font': buyer_title_font,
        'buyer_body_font': buyer_body_font,
        'buyer_note_font': buyer_note_font,
        'buyer_h': buyer_h,
        'footer_lines': footer_lines,
        'footer_font': footer_font,
        'stack': stack,
        'bottom_gap': available - stack,
    }


def _compress_to_safe_area(layout: dict, canvas: _Canvas) -> dict:
    """Ужимает промежутки, если даже минимальный масштаб не входит в безопасную зону."""
    available = canvas.safe_bottom - canvas.safe_top
    overflow = layout['stack'] - available
    if overflow <= 0:
        return layout
    tightened = dict(layout)
    min_gap = 8
    current_gap = tightened['section_gap']
    reducible = max(0, current_gap - min_gap) * 3 + max(0, 18 - min_gap)
    if reducible <= 0:
        return tightened
    reduction = min(overflow, reducible)
    gap_cut = min(current_gap - min_gap, reduction // 3)
    tightened['section_gap'] = current_gap - gap_cut
    tightened['stack'] = layout['stack'] - gap_cut * 3
    tightened['bottom_gap'] = available - tightened['stack']
    return tightened


def _paint(draw: ImageDraw.ImageDraw, content: InstagramCardContent, canvas: _Canvas, layout: dict) -> None:
    leftover = max(0, layout['bottom_gap'])
    extra_gap = leftover // 4
    section_gap = layout['section_gap'] + extra_gap
    y = canvas.safe_top
    left = canvas.pad_x
    right = canvas.width - canvas.pad_x
    inner = layout['inner']

    brand_font = layout['brand_font']
    tag_font = layout['tag_font']
    brand_w, brand_h = _text_size(draw, BRAND, brand_font)
    tag_widths = [_text_size(draw, line, tag_font)[0] for line in TAGLINE_LINES]
    tag_block_w = max(tag_widths) if tag_widths else 0
    row_h = layout['brand_row']
    brand_y = y + max(0, (row_h - brand_h) // 2)
    draw.text((left, brand_y), BRAND, font=brand_font, fill=COLOR_RED)
    divider_x = left + brand_w + 18
    tag_x = divider_x + 16
    if tag_x + tag_block_w <= right:
        draw.line((divider_x, y + 6, divider_x, y + row_h - 6), fill=COLOR_DIVIDER, width=2)
        tag_h = _block_height(draw, list(TAGLINE_LINES), tag_font, 2)
        tag_y = y + max(0, (row_h - tag_h) // 2)
        _draw_lines(draw, list(TAGLINE_LINES), x=tag_x, y=tag_y, font=tag_font, fill=COLOR_INK, gap=2)
    else:
        y += row_h + 8
        _draw_lines(draw, list(TAGLINE_LINES), x=left, y=y, font=tag_font, fill=COLOR_INK, gap=2)
        y += _block_height(draw, list(TAGLINE_LINES), tag_font, 2)
        row_h = 0
    y += row_h + 18

    y = _draw_lines(
        draw,
        layout['headline_lines'],
        x=left,
        y=y,
        font=layout['headline_font'],
        fill=COLOR_INK,
        gap=4,
    )
    y += section_gap

    card_top = y
    card_bottom = y + layout['card_h']
    draw.rounded_rectangle(
        (left, card_top, right, card_bottom),
        radius=28,
        fill=COLOR_CARD,
        outline=COLOR_CARD_LINE,
        width=2,
    )
    cursor = card_top + layout['card_pad']
    badge_font = layout['badge_font']
    badge_text_w, badge_text_h = _text_size(draw, BADGE, badge_font)
    badge_w = badge_text_w + 28
    badge_h = layout['badge_h']
    draw.rounded_rectangle(
        (left + layout['card_pad'], cursor, left + layout['card_pad'] + badge_w, cursor + badge_h),
        radius=badge_h // 2,
        fill=COLOR_RED,
    )
    draw.text(
        (left + layout['card_pad'] + 14, cursor + (badge_h - badge_text_h) // 2 - 1),
        BADGE,
        font=badge_font,
        fill=COLOR_WHITE,
    )
    if content.request_number:
        number_font = layout['number_font']
        number_w, number_h = _text_size(draw, content.request_number, number_font)
        number_x = right - layout['card_pad'] - number_w
        min_x = left + layout['card_pad'] + badge_w + 16
        if number_x >= min_x:
            draw.text(
                (number_x, cursor + (badge_h - number_h) // 2),
                content.request_number,
                font=number_font,
                fill=COLOR_MUTED,
            )
        else:
            cursor += badge_h + 8
            draw.text((left + layout['card_pad'], cursor), content.request_number, font=number_font, fill=COLOR_MUTED)
            cursor += _line_height(draw, number_font)
            badge_h = 0
    cursor += badge_h + 18

    if layout['vehicle_lines']:
        cursor = _draw_lines(
            draw,
            layout['vehicle_lines'],
            x=left + layout['card_pad'],
            y=cursor,
            font=layout['vehicle_font'],
            fill=COLOR_INK,
            gap=layout['gap'],
        )
        cursor += 12
    if layout['part_lines']:
        cursor = _draw_lines(
            draw,
            layout['part_lines'],
            x=left + layout['card_pad'],
            y=cursor,
            font=layout['part_font'],
            fill=COLOR_RED,
            gap=layout['gap'],
        )
        cursor += 14
    if layout['location_lines']:
        pin_y = cursor + 4
        _draw_pin(draw, left + layout['card_pad'], pin_y)
        _draw_lines(
            draw,
            layout['location_lines'],
            x=left + layout['card_pad'] + 32,
            y=cursor,
            font=layout['location_font'],
            fill=COLOR_BODY,
            gap=4,
        )

    y = card_bottom + section_gap
    y = _paint_cta(
        draw,
        y=y,
        left=left,
        right=right,
        height=layout['seller_h'],
        fill=COLOR_RED,
        title=layout['seller_title'],
        body=layout['seller_body'],
        note=layout['seller_note'],
        title_font=layout['seller_title_font'],
        body_font=layout['seller_body_font'],
        note_font=layout['seller_note_font'],
        title_fill=COLOR_WHITE,
        body_fill=COLOR_WHITE,
        note_fill=COLOR_WHITE,
    )
    y += section_gap
    y = _paint_cta(
        draw,
        y=y,
        left=left,
        right=right,
        height=layout['buyer_h'],
        fill=COLOR_PINK,
        title=layout['buyer_title'],
        body=layout['buyer_body'],
        note=layout['buyer_note'],
        title_font=layout['buyer_title_font'],
        body_font=layout['buyer_body_font'],
        note_font=layout['buyer_note_font'],
        title_fill=COLOR_INK,
        body_fill=COLOR_BODY,
        note_fill=COLOR_RED,
    )
    y += 18 + extra_gap
    _draw_lines(
        draw,
        layout['footer_lines'],
        x=left,
        y=y,
        font=layout['footer_font'],
        fill=COLOR_FOOTER,
        gap=4,
        align='center',
        width=inner,
    )


def _paint_cta(
    draw: ImageDraw.ImageDraw,
    *,
    y: int,
    left: int,
    right: int,
    height: int,
    fill: tuple[int, int, int],
    title: list[str],
    body: list[str],
    note: list[str],
    title_font,
    body_font,
    note_font,
    title_fill,
    body_fill,
    note_fill,
) -> int:
    draw.rounded_rectangle((left, y, right, y + height), radius=24, fill=fill)
    cursor = y + 26
    cursor = _draw_lines(draw, title, x=left + 28, y=cursor, font=title_font, fill=title_fill, gap=4)
    cursor += 8
    cursor = _draw_lines(draw, body, x=left + 28, y=cursor, font=body_font, fill=body_fill, gap=4)
    cursor += 10
    _draw_lines(draw, note, x=left + 28, y=cursor, font=note_font, fill=note_fill, gap=4)
    return y + height


def _draw_pin(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    draw.ellipse((x, y, x + 18, y + 18), fill=COLOR_RED)
    draw.ellipse((x + 5, y + 5, x + 13, y + 13), fill=COLOR_WHITE)
