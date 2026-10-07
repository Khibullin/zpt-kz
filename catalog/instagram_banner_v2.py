"""Программный баннер заявки для ленты Instagram.

Утверждённый макет: типографика, категория детали и два информационных CTA.
Фотографии автомобиля и запчастей не используются.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

from catalog.instagram_public_text import (
    PublicPartRequest,
    build_banner_v2_caption,
    build_public_part_request,
)

logger = logging.getLogger(__name__)

BANNER_SIZE = (1080, 1350)
_WIDTH, _HEIGHT = BANNER_SIZE
_PAD_X = 60
_FOOTER_HEIGHT = 204
_CTA_HEIGHT = 208
_CTA_GAP = 18
_MAX_VISIBLE_PARTS = 3

_BG = (245, 246, 247)
_WHITE = (255, 255, 255)
_INK = (24, 27, 32)
_MUTED = (90, 96, 104)
_RED = (227, 30, 36)
_BLUE = (23, 105, 194)
_ICON_BG = (236, 239, 242)
_DECOR = (228, 231, 235)
_FOOTER = (24, 27, 32)
_FOOTER_MUTED = (168, 174, 182)

PART_HEADLIGHT = 'headlight'
PART_BODY = 'body'
PART_ENGINE = 'engine'
PART_BRAKES = 'brakes'
PART_SUSPENSION = 'suspension'
PART_FILTER = 'filter'
PART_ELECTRICAL = 'electrical'
PART_TRANSMISSION = 'transmission'
PART_WHEEL = 'wheel'
PART_GENERIC = 'generic_part'

_CATEGORY_MARKERS = (
    (PART_HEADLIGHT, ('фар', 'фонар', 'lighting')),
    (PART_BRAKES, ('колод', 'суппорт', 'тормоз')),
    (PART_SUSPENSION, ('амортиз', 'рычаг', 'подвеск', 'стойк')),
    (PART_FILTER, ('фильтр',)),
    (PART_TRANSMISSION, ('кпп', 'коробк', 'сцеплен', 'акпп', 'мкпп')),
    (PART_WHEEL, ('диск', 'колес', 'шин')),
    (PART_ELECTRICAL, ('генератор', 'стартер', 'аккумулятор', 'провод', 'датчик', 'катуш')),
    (PART_ENGINE, ('двигат', 'мотор', 'радиатор', 'помпа')),
    (PART_BODY, (
        'бампер', 'капот', 'крыл', 'двер', 'порог', 'зеркал', 'стекл',
        'решет', 'решёт', 'подкрыл',
    )),
)


@dataclass(frozen=True)
class BannerContent:
    brand: str = ''
    model: str = ''
    year: str = ''
    parts: tuple[str, ...] = ()
    hidden_part_count: int = 0
    city: str = ''
    request_number: str = ''


@dataclass(frozen=True)
class BannerRender:
    image: Image.Image
    content_bottom: int


def part_category(label: str) -> str:
    """Категория иконки. Не определяет конкретную деталь конкретного автомобиля."""
    text = ' '.join(str(label or '').casefold().split())
    for category, markers in _CATEGORY_MARKERS:
        if any(marker in text for marker in markers):
            return category
    return PART_GENERIC


def fit_text_to_width(draw, text, max_width, *, max_size, min_size=20, bold=True):
    """Уменьшает кегль, пока строка не входит в ширину. Ниже min_size не опускается."""
    size = int(max_size)
    floor = int(min_size)
    font = _font(size, bold=bold)
    while size > floor and _text_size(draw, text, font)[0] > max_width:
        size -= 2
        font = _font(size, bold=bold)
    return font


def render_request_banner_v2(content: BannerContent) -> BannerRender:
    """Рисует утверждённый типографический баннер 1080×1350."""
    image = Image.new('RGB', BANNER_SIZE, _BG)
    draw = ImageDraw.Draw(image)
    _draw_decor(draw)
    header_bottom = _draw_header(image, draw)
    title_bottom = _draw_title(draw, content, header_bottom + 22)
    meta_bottom = _draw_meta_line(draw, content, title_bottom + 18)
    seller_box, buyer_box = _cta_boxes()
    _draw_parts(draw, content, meta_bottom + 26, seller_box[1] - 22)
    _draw_seller_cta(draw, seller_box)
    _draw_buyer_cta(draw, buyer_box)
    _draw_footer(image, draw)
    return BannerRender(image=image, content_bottom=seller_box[3])


def render_development_preview(content: BannerContent, output_path: Path) -> Path:
    """Локальный просмотр композиции. Не создаёт публикацию и не вызывает Meta."""
    rendered = render_request_banner_v2(content)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered.image.convert('RGB').save(output_path, format='JPEG', quality=92, optimize=True)
    return output_path


def render_and_save_feed_banner(product_request) -> tuple[Path, str]:
    from catalog.image_generator import InstagramStoryGenerationError

    if product_request is None or product_request.pk is None:
        raise InstagramStoryGenerationError('Для генерации нужна сохранённая заявка.')

    public = build_public_part_request(product_request)
    caption = build_banner_v2_caption(public)
    content = _content_from_request(public)
    try:
        rendered = render_request_banner_v2(content)
        output_dir = Path(settings.MEDIA_ROOT) / 'instagram_feed'
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        output_path = output_dir / f'feed_{product_request.pk}_{timestamp}.jpg'
        rendered.image.convert('RGB').save(output_path, format='JPEG', quality=92, optimize=True)
        return output_path.resolve(), caption
    except InstagramStoryGenerationError:
        raise
    except Exception as exc:
        logger.exception(
            'Ошибка генерации Instagram banner v2 для заявки %s',
            product_request.pk,
        )
        raise InstagramStoryGenerationError('Не удалось сгенерировать изображение.') from exc


def _content_from_request(public: PublicPartRequest) -> BannerContent:
    visible = tuple(public.parts[:_MAX_VISIBLE_PARTS])
    hidden = max(0, len(public.parts) - len(visible))
    return BannerContent(
        brand=public.brand,
        model=public.model,
        year=public.year,
        parts=visible,
        hidden_part_count=hidden,
        city=public.city,
        request_number=public.public_request_number,
    )


def _cta_boxes() -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    top = _HEIGHT - _FOOTER_HEIGHT - 28 - _CTA_HEIGHT - 76
    total = _WIDTH - _PAD_X * 2 - _CTA_GAP
    seller_width = int(total * 0.54)
    left = _PAD_X
    seller = (left, top, left + seller_width, top + _CTA_HEIGHT)
    buyer_left = seller[2] + _CTA_GAP
    buyer = (buyer_left, top, _WIDTH - _PAD_X, top + _CTA_HEIGHT)
    return seller, buyer


def _font(size: int, *, bold: bool) -> ImageFont.ImageFont:
    size = max(13, int(size))
    names = (
        ['NotoSans-Bold.ttf', 'Inter-Bold.ttf', 'DejaVuSans-Bold.ttf']
        if bold
        else ['NotoSans-Regular.ttf', 'Inter-Regular.ttf', 'DejaVuSans.ttf']
    )
    candidates = [Path(settings.BASE_DIR) / 'static' / 'fonts' / name for name in names]
    candidates.extend([
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
        Path('C:/Windows/Fonts/arialbd.ttf' if bold else 'C:/Windows/Fonts/arial.ttf'),
        Path('C:/Windows/Fonts/segoeuib.ttf' if bold else 'C:/Windows/Fonts/segoeui.ttf'),
    ])
    for candidate in candidates:
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _line_step(draw, text: str, font, gap: int = 4) -> int:
    """Шаг до следующей строки от точки draw.text, с учётом внутреннего отступа шрифта."""
    _left, _top, _right, bottom = draw.textbbox((0, 0), text or 'Ay', font=font)
    return bottom + gap


def _wrap(draw, text: str, font, max_width: int, max_lines: int) -> list[str]:
    words = ' '.join(str(text or '').split()).split()
    if not words:
        return []
    lines: list[str] = []
    current = ''
    for word in words:
        candidate = word if not current else f'{current} {word}'
        if _text_size(draw, candidate, font)[0] <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if lines and len(words) > sum(len(line.split()) for line in lines):
        lines[-1] = _ellipsize(draw, lines[-1], font, max_width)
    elif lines and _text_size(draw, lines[-1], font)[0] > max_width:
        lines[-1] = _ellipsize(draw, lines[-1], font, max_width)
    return lines


def _ellipsize(draw, text: str, font, max_width: int) -> str:
    value = text.rstrip(' .,;')
    if _text_size(draw, value, font)[0] <= max_width:
        return value
    trimmed = value
    while trimmed and _text_size(draw, f'{trimmed}…', font)[0] > max_width:
        trimmed = trimmed[:-1]
    return f'{trimmed}…' if trimmed else '…'


def _logo_image() -> Image.Image | None:
    path = Path(settings.BASE_DIR) / 'static' / 'images' / 'logo.png'
    if not path.is_file():
        return None
    try:
        return Image.open(path).convert('RGBA')
    except OSError:
        return None


def _draw_decor(draw: ImageDraw.ImageDraw) -> None:
    """Светлая техническая графика. Не изображает автомобиль или деталь."""
    for index in range(6):
        start_y = 640 + index * 36
        draw.line((700, start_y, 1000, start_y - 150), fill=_DECOR, width=2)
    draw.arc((820, 680, 1180, 1040), start=200, end=330, fill=_DECOR, width=2)
    draw.arc((860, 720, 1140, 1000), start=200, end=330, fill=_DECOR, width=1)


def _draw_header(image: Image.Image, draw: ImageDraw.ImageDraw) -> int:
    top = 32
    logo = _logo_image()
    if logo is not None:
        logo.thumbnail((236, 62), Image.Resampling.LANCZOS)
        image.paste(logo, (_PAD_X, top), logo)
        text_top = top + logo.height + 10
        block_height = logo.height
    else:
        text_top = top
        block_height = 0
    tag_font = _font(15, bold=True)
    tag_lines = ('МАРКЕТПЛЕЙС', 'АВТОЗАПЧАСТЕЙ', 'КАЗАХСТАНА')
    cursor = text_top
    for line in tag_lines:
        draw.text((_PAD_X, cursor), line, font=tag_font, fill=_INK)
        cursor += _line_step(draw, line, tag_font, gap=2)
    left_bottom = cursor
    left_center = top + max(block_height, left_bottom - top) // 2
    _draw_search_badge(draw, left_center)
    return max(left_bottom, top + 150)


def _draw_search_badge(draw: ImageDraw.ImageDraw, center_y: int) -> None:
    label = 'ПОКУПАТЕЛЬ ИЩЕТ'
    font = _font(22, bold=True)
    text_w, text_h = _text_size(draw, label, font)
    icon = 22
    pad_x = 16
    pad_y = 12
    width = pad_x + icon + 10 + text_w + pad_x
    height = max(text_h, icon) + pad_y * 2
    right = _WIDTH - _PAD_X
    left = right - width
    top = int(center_y - height / 2)
    draw.rounded_rectangle((left, top, right, top + height), radius=12, fill=_RED)
    icon_top = top + (height - icon) // 2
    _icon_search(draw, (left + pad_x, icon_top, left + pad_x + icon, icon_top + icon), _WHITE)
    text_y = top + (height - text_h) // 2 - 1
    draw.text((left + pad_x + icon + 10, text_y), label, font=font, fill=_WHITE)


def _draw_title(draw: ImageDraw.ImageDraw, content: BannerContent, top: int) -> int:
    max_width = _WIDTH - _PAD_X * 2
    y = top
    rows = (
        (' '.join(content.brand.split()).upper(), _INK),
        (' '.join(content.model.split()).upper(), _RED),
    )
    drawn = False
    for text, color in rows:
        if not text:
            continue
        font, lines = _fit_title_lines(draw, text, max_width)
        for line in lines:
            draw.text((_PAD_X, y), line, font=font, fill=color)
            y += _line_step(draw, line, font, gap=2)
        drawn = True
    if not drawn:
        font = _font(64, bold=True)
        draw.text((_PAD_X, y), 'ЗАЯВКА', font=font, fill=_INK)
        y += _line_step(draw, 'ЗАЯВКА', font, gap=0)
    return y


def _fit_title_lines(draw, text: str, max_width: int) -> tuple[ImageFont.ImageFont, list[str]]:
    font = fit_text_to_width(draw, text, max_width, max_size=72, min_size=36)
    if _text_size(draw, text, font)[0] <= max_width:
        return font, [text]
    font = _font(36, bold=True)
    return font, _wrap(draw, text, font, max_width, 2) or [text]


def _draw_meta_line(draw: ImageDraw.ImageDraw, content: BannerContent, top: int) -> int:
    segments: list[tuple[str, tuple[int, int, int]]] = []
    if content.year:
        segments.append((content.year, _INK))
    if content.city:
        segments.append((content.city, _INK))
    if content.request_number:
        segments.append((f'Заявка №{content.request_number}', _RED))
    if not segments:
        return top
    max_width = _WIDTH - _PAD_X * 2
    probe = '   '.join(text for text, _color in segments)
    font = fit_text_to_width(draw, probe, max_width, max_size=30, min_size=20)
    text_h = max(_line_step(draw, text, font, gap=0) for text, _color in segments)
    gap = 14
    bar_w = 2
    x = _PAD_X
    y = top
    for index, (text, color) in enumerate(segments):
        if index:
            bar_x = x
            bar_top = y + max(0, (text_h - 18) // 2)
            draw.rectangle((bar_x, bar_top, bar_x + bar_w, bar_top + 18), fill=_RED)
            x += bar_w + gap
        draw.text((x, y), text, font=font, fill=color)
        x += _text_size(draw, text, font)[0] + gap
    return y + text_h


def _visible_parts(content: BannerContent) -> tuple[list[str], int]:
    labels = [part for part in content.parts if part]
    visible = labels[:_MAX_VISIBLE_PARTS]
    extra = content.hidden_part_count
    if extra <= 0 and len(labels) > _MAX_VISIBLE_PARTS:
        extra = len(labels) - _MAX_VISIBLE_PARTS
    return visible, extra


def _draw_parts(draw, content: BannerContent, top: int, bottom: int) -> None:
    labels, extra = _visible_parts(content)
    slots = len(labels) + (1 if extra else 0)
    if slots <= 0 or bottom <= top:
        return
    available = bottom - top
    slot_h = min(84, available / slots)
    text_width = _WIDTH - _PAD_X * 2 - 86
    y = float(top)
    for label in labels:
        font_size = 36 if slot_h >= 72 else 30 if slot_h >= 58 else 24
        max_lines = 2 if slot_h >= 70 else 1
        font = _font(font_size, bold=True)
        lines = _wrap(draw, label, font, text_width, max_lines) or [_ellipsize(draw, label, font, text_width)]
        block_h = sum(_line_step(draw, line, font, gap=2) for line in lines)
        icon = 56 if slot_h >= 72 else 44
        row_top = y + max(0, (slot_h - max(icon, block_h)) / 2)
        _draw_part_icon(draw, part_category(label), _PAD_X, int(row_top), icon)
        text_y = row_top + max(0, (icon - block_h) / 2)
        for line in lines:
            draw.text((_PAD_X + icon + 16, text_y), line, font=font, fill=_INK)
            text_y += _line_step(draw, line, font, gap=2)
        y += slot_h
    if extra:
        more_font = _font(28, bold=True)
        more = _more_parts_label(extra)
        more_h = _text_size(draw, more, more_font)[1]
        draw.text(
            (_PAD_X + 72, y + max(0, (slot_h - more_h) / 2)),
            more,
            font=more_font,
            fill=_MUTED,
        )


def _draw_part_icon(draw, category: str, x: int, y: int, size: int) -> None:
    draw.ellipse((x, y, x + size, y + size), fill=_ICON_BG)
    pad = max(12, size // 4)
    box = (x + pad, y + pad, x + size - pad, y + size - pad)
    drawer = {
        PART_HEADLIGHT: _icon_headlight,
        PART_BODY: _icon_body,
        PART_ENGINE: _icon_engine,
        PART_BRAKES: _icon_brakes,
        PART_SUSPENSION: _icon_suspension,
        PART_FILTER: _icon_filter,
        PART_ELECTRICAL: _icon_electrical,
        PART_TRANSMISSION: _icon_transmission,
        PART_WHEEL: _icon_wheel,
    }.get(category, _icon_generic)
    drawer(draw, box, _INK)


def _icon_search(draw, box, color) -> None:
    x0, y0, x1, y1 = box
    radius = (x1 - x0) * 0.34
    cx = x0 + radius + 1
    cy = y0 + radius + 1
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=color, width=3)
    draw.line((cx + radius * 0.7, cy + radius * 0.7, x1 - 1, y1 - 1), fill=color, width=3)


def _icon_shop(draw, box, color) -> None:
    x0, y0, x1, y1 = box
    mid = (x0 + x1) / 2
    draw.polygon([(x0, y0 + (y1 - y0) * 0.28), (mid, y0), (x1, y0 + (y1 - y0) * 0.28)], outline=color)
    draw.line(
        [(x0, y0 + (y1 - y0) * 0.28), (mid, y0), (x1, y0 + (y1 - y0) * 0.28)],
        fill=color,
        width=3,
    )
    draw.rounded_rectangle((x0 + 3, y0 + (y1 - y0) * 0.34, x1 - 3, y1), radius=2, outline=color, width=3)
    door_w = (x1 - x0) * 0.22
    draw.rectangle((mid - door_w / 2, y0 + (y1 - y0) * 0.58, mid + door_w / 2, y1 - 2), outline=color, width=2)


def _icon_request(draw, box, color) -> None:
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0 + 2, y0, x1 - 4, y1), radius=3, outline=color, width=3)
    fold = (x1 - x0) * 0.28
    draw.line((x1 - 4 - fold, y0, x1 - 4, y0 + fold), fill=color, width=2)
    inset = (x1 - x0) * 0.18
    for step in (0.38, 0.54, 0.70):
        y = y0 + (y1 - y0) * step
        draw.line((x0 + inset, y, x1 - inset - 4, y), fill=color, width=2)


def _icon_headlight(draw, box, color) -> None:
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0, y0 + 3, x1, y1 - 3), radius=6, outline=color, width=3)
    draw.ellipse((x0 + (x1 - x0) * 0.48, y0 + 7, x1 - 4, y1 - 7), outline=color, width=3)


def _icon_body(draw, box, color) -> None:
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0, y0 + 2, x1, y1 - 2), radius=4, outline=color, width=3)
    mid = y0 + (y1 - y0) * 0.55
    draw.line((x0 + 4, mid, x1 - 4, mid), fill=color, width=2)


def _icon_engine(draw, box, color) -> None:
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0 + 2, y0 + 8, x1 - 2, y1), radius=3, outline=color, width=3)
    cap = (x1 - x0) / 5
    draw.rectangle((x0 + cap, y0, x0 + cap * 2, y0 + 8), outline=color, width=2)
    draw.rectangle((x1 - cap * 2, y0, x1 - cap, y0 + 8), outline=color, width=2)


def _icon_brakes(draw, box, color) -> None:
    draw.ellipse(box, outline=color, width=3)
    x0, y0, x1, y1 = box
    inset = (x1 - x0) * 0.28
    draw.ellipse((x0 + inset, y0 + inset, x1 - inset, y1 - inset), outline=color, width=3)


def _icon_suspension(draw, box, color) -> None:
    x0, y0, x1, y1 = box
    mid = (x0 + x1) / 2
    span = (x1 - x0) * 0.28
    points = [(mid, y0)]
    steps = 5
    for index in range(steps):
        direction = -1 if index % 2 else 1
        points.append((mid + direction * span, y0 + (y1 - y0) * (index + 1) / steps))
    draw.line(points, fill=color, width=3)


def _icon_filter(draw, box, color) -> None:
    x0, y0, x1, y1 = box
    draw.rectangle((x0, y0, x1, y1), outline=color, width=3)
    for step in range(1, 4):
        x = x0 + (x1 - x0) * step / 4
        draw.line((x, y0 + 3, x, y1 - 3), fill=color, width=2)


def _icon_electrical(draw, box, color) -> None:
    x0, y0, x1, y1 = box
    mid_x = (x0 + x1) / 2
    mid_y = (y0 + y1) / 2
    draw.polygon(
        [
            (mid_x + 2, y0),
            (x0 + 2, mid_y),
            (mid_x - 2, mid_y),
            (mid_x - 6, y1),
            (x1 - 2, mid_y - 2),
            (mid_x + 4, mid_y - 2),
        ],
        fill=color,
    )


def _icon_transmission(draw, box, color) -> None:
    x0, y0, x1, y1 = box
    radius = min(x1 - x0, y1 - y0) * 0.34
    cy = (y0 + y1) / 2
    draw.ellipse((x0, cy - radius, x0 + radius * 2, cy + radius), outline=color, width=3)
    draw.ellipse((x1 - radius * 2, cy - radius, x1, cy + radius), outline=color, width=3)


def _icon_wheel(draw, box, color) -> None:
    draw.ellipse(box, outline=color, width=3)
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    draw.line((cx, y0 + 3, cx, y1 - 3), fill=color, width=2)
    draw.line((x0 + 3, cy, x1 - 3, cy), fill=color, width=2)


def _icon_generic(draw, box, color) -> None:
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=4, outline=color, width=3)
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    arm = min(x1 - x0, y1 - y0) * 0.22
    draw.line((cx - arm, cy, cx + arm, cy), fill=color, width=3)
    draw.line((cx, cy - arm, cx, cy + arm), fill=color, width=3)


def _seller_headline(draw, box) -> tuple[str, ...]:
    """Одна строка, если заголовок входит в блок. Иначе допустим перенос."""
    x0, _y0, x1, _y1 = box
    max_width = x1 - (x0 + 22 + 48 + 14) - 16
    headline = 'ПРОДАЁТЕ ЗАПЧАСТИ?'
    font = fit_text_to_width(draw, headline, max_width, max_size=28, min_size=20)
    if _text_size(draw, headline, font)[0] <= max_width:
        return (headline,)
    return ('ПРОДАЁТЕ', 'ЗАПЧАСТИ?')


def _draw_seller_cta(draw, box) -> None:
    draw.rounded_rectangle(box, radius=22, fill=_RED)
    _draw_cta_copy(
        draw,
        box,
        _seller_headline(draw, box),
        ('ПОЛУЧАЙТЕ ЗАЯВКИ', 'НА ZPT.KZ'),
        _WHITE,
        _icon_shop,
        main_size=28,
        sub_size=22,
    )


def _draw_buyer_cta(draw, box) -> None:
    draw.rounded_rectangle(box, radius=22, fill=_WHITE, outline=_BLUE, width=3)
    _draw_cta_copy(
        draw,
        box,
        ('ИЩЕТЕ', 'ЗАПЧАСТЬ?'),
        ('ОСТАВЬТЕ ЗАЯВКУ', 'НА ZPT.KZ'),
        _BLUE,
        _icon_request,
        main_size=28,
        sub_size=20,
    )


def _draw_cta_copy(draw, box, primary, secondary, color, icon_draw, *, main_size, sub_size) -> None:
    x0, y0, x1, y1 = box
    pad = 22
    icon = 48
    text_x = x0 + pad + icon + 14
    max_width = x1 - text_x - 16
    main_font = fit_text_to_width(
        draw,
        max(primary, key=len),
        max_width,
        max_size=main_size,
        min_size=18,
    )
    sub_font = fit_text_to_width(
        draw,
        max(secondary, key=len),
        max_width,
        max_size=sub_size,
        min_size=15,
    )
    lines = [(line, main_font) for line in primary] + [(line, sub_font) for line in secondary]
    steps = [_line_step(draw, text, font, gap=6) for text, font in lines]
    block_h = sum(steps) - 6
    text_y = y0 + max(pad, (y1 - y0 - block_h) // 2)
    icon_y = y0 + max(pad, (y1 - y0 - icon) // 2)
    icon_draw(draw, (x0 + pad, icon_y, x0 + pad + icon, icon_y + icon), color)
    for (text, font), step in zip(lines, steps):
        draw.text((text_x, text_y), text, font=font, fill=color)
        text_y += step


def _more_parts_label(count: int) -> str:
    tail = count % 10
    hundred = count % 100
    if tail == 1 and hundred != 11:
        word = 'деталь'
    elif tail in (2, 3, 4) and hundred not in (12, 13, 14):
        word = 'детали'
    else:
        word = 'деталей'
    return f'+ ещё {count} {word}'


def _draw_footer(image: Image.Image, draw: ImageDraw.ImageDraw) -> None:
    top = _HEIGHT - _FOOTER_HEIGHT
    draw.rectangle((0, top, _WIDTH, _HEIGHT), fill=_FOOTER)
    logo = _logo_image()
    text_x = _PAD_X
    if logo is not None:
        logo.thumbnail((180, 46), Image.Resampling.LANCZOS)
        image.paste(logo, (_PAD_X, top + 28), logo)
        text_x = _PAD_X
        tag_top = top + 28 + logo.height + 8
    else:
        brand_font = _font(28, bold=True)
        draw.text((_PAD_X, top + 32), 'ZPT.KZ', font=brand_font, fill=_WHITE)
        tag_top = top + 72
    tag_font = _font(18, bold=False)
    tag = 'Маркетплейс автозапчастей Казахстана'
    draw.text((text_x, tag_top), tag, font=tag_font, fill=_FOOTER_MUTED)
    _draw_benefits(draw, top + 36)
    slogan_font = _font(15, bold=False)
    slogan = 'ДВИЖЕМ АВТОМОБИЛИ ВПЕРЁД'
    slogan_w, _slogan_h = _text_size(draw, slogan, slogan_font)
    draw.text(
        ((_WIDTH - slogan_w) // 2, _HEIGHT - 34),
        slogan,
        font=slogan_font,
        fill=(120, 126, 134),
    )


def _draw_benefits(draw, top: int) -> None:
    items = (
        ('УДОБНЫЙ', 'ПОИСК', _icon_search),
        ('НАДЁЖНЫЕ', 'ПРОДАВЦЫ', _icon_sellers),
        ('БЫСТРЫЙ', 'ПОДБОР', _icon_match),
    )
    column_w = 150
    start = _WIDTH - _PAD_X - column_w * len(items)
    font = _font(15, bold=True)
    for index, (first, second, icon_draw) in enumerate(items):
        x = start + index * column_w
        icon = 26
        icon_draw(draw, (x, top, x + icon, top + icon), _WHITE)
        draw.text((x + icon + 8, top - 1), first, font=font, fill=_WHITE)
        draw.text((x + icon + 8, top + 18), second, font=font, fill=_FOOTER_MUTED)


def _icon_sellers(draw, box, color) -> None:
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2
    radius = (x1 - x0) * 0.22
    draw.ellipse((cx - radius, y0, cx + radius, y0 + radius * 2), outline=color, width=2)
    draw.arc((x0 + 2, y0 + radius * 1.6, x1 - 2, y1 + 4), start=200, end=340, fill=color, width=2)


def _icon_match(draw, box, color) -> None:
    x0, y0, x1, y1 = box
    mid_y = (y0 + y1) / 2
    draw.line((x0 + 2, mid_y, x0 + (x1 - x0) * 0.35, y1 - 3), fill=color, width=3)
    draw.line((x0 + (x1 - x0) * 0.35, y1 - 3, x1 - 2, y0 + 3), fill=color, width=3)
