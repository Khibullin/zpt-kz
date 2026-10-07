"""Программный баннер заявки для ленты Instagram.

Генеративная модель не рисует баннер. Текст и геометрия собираются из
публичного представления заявки.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

from catalog.instagram_public_text import (
    PublicPartRequest,
    build_banner_v2_caption,
    build_public_part_request,
)
from catalog.instagram_visuals import (
    SOURCE_GENERIC,
    VehicleVisualResolver,
    resolve_customer_part_photos,
)

logger = logging.getLogger(__name__)

BANNER_SIZE = (1080, 1350)
_WIDTH, _HEIGHT = BANNER_SIZE
_FOOTER_HEIGHT = 118
_PAD_X = 64

_BG = (244, 245, 247)
_WHITE = (255, 255, 255)
_INK = (28, 31, 36)
_MUTED = (90, 96, 104)
_RED = (227, 30, 36)
_BLUE = (20, 92, 184)
_FOOTER = (24, 26, 30)
_LINE = (214, 218, 224)

_CTA_RESERVE = 380
_MAX_VISIBLE_PARTS = 3


@dataclass(frozen=True)
class BannerContent:
    brand: str = ''
    model: str = ''
    year: str = ''
    parts: tuple[str, ...] = ()
    hidden_part_count: int = 0
    city: str = ''
    request_number: str = ''
    vehicle_image: Image.Image | None = None
    vehicle_is_generic: bool = True
    part_images: tuple[Image.Image, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BannerRender:
    image: Image.Image
    content_bottom: int


def render_request_banner_v2(content: BannerContent) -> BannerRender:
    image = Image.new('RGB', BANNER_SIZE, _BG)
    draw = ImageDraw.Draw(image)
    bottom_limit = _HEIGHT - _FOOTER_HEIGHT - 24
    cursor = 48

    cursor = _draw_kicker(draw, cursor)
    cursor = _draw_vehicle_title(draw, content, cursor)
    cursor = _paste_vehicle(image, content, cursor)
    cursor = _draw_parts(draw, image, content, cursor, bottom_limit)
    cursor = _draw_meta(draw, content, cursor)
    cursor = _draw_cta_stack(draw, cursor, bottom_limit)
    _draw_footer(draw)
    return BannerRender(image=image, content_bottom=min(cursor, bottom_limit))


def render_and_save_feed_banner(product_request) -> tuple[Path, str]:
    from catalog.image_generator import InstagramStoryGenerationError

    if product_request is None or product_request.pk is None:
        raise InstagramStoryGenerationError('Для генерации нужна сохранённая заявка.')

    public = build_public_part_request(product_request)
    caption = build_banner_v2_caption(public)
    content = _content_from_request(product_request, public)
    try:
        rendered = render_request_banner_v2(content)
        output_dir = Path(settings.MEDIA_ROOT) / 'instagram_feed'
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        output_path = output_dir / f'feed_{product_request.pk}_{timestamp}.jpg'
        rendered.image.convert('RGB').save(output_path, format='JPEG', quality=90, optimize=True)
        return output_path.resolve(), caption
    except InstagramStoryGenerationError:
        raise
    except Exception as exc:
        logger.exception(
            'Ошибка генерации Instagram banner v2 для заявки %s',
            product_request.pk,
        )
        raise InstagramStoryGenerationError('Не удалось сгенерировать изображение.') from exc


def _content_from_request(product_request, public: PublicPartRequest) -> BannerContent:
    year = None
    if public.year.isdigit():
        year = int(public.year)
    vehicle = VehicleVisualResolver().resolve(
        brand=public.brand,
        model=public.model,
        year=year,
    )
    visible = tuple(public.parts[:_MAX_VISIBLE_PARTS])
    hidden = max(0, len(public.parts) - len(visible))
    photos = tuple(
        asset.image
        for asset in resolve_customer_part_photos(product_request)
        if asset.image is not None
    )
    return BannerContent(
        brand=public.brand,
        model=public.model,
        year=public.year,
        parts=visible,
        hidden_part_count=hidden,
        city=public.city,
        request_number=public.public_request_number,
        vehicle_image=vehicle.image,
        vehicle_is_generic=vehicle.source_type == SOURCE_GENERIC,
        part_images=photos,
    )


def _font(size: int, *, bold: bool) -> ImageFont.ImageFont:
    size = max(18, int(size))
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
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if lines and len(words) > sum(len(line.split()) for line in lines):
        lines[-1] = lines[-1].rstrip(' .,;') + '…'
    return lines


def _draw_left(draw, text, x, y, font, fill) -> int:
    draw.text((x, y), text, font=font, fill=fill)
    return y + _text_size(draw, text, font)[1]


def _draw_kicker(draw, cursor: int) -> int:
    font = _font(28, bold=True)
    draw.rounded_rectangle(( _PAD_X, cursor + 6, _PAD_X + 16, cursor + 22), radius=3, fill=_RED)
    _draw_left(draw, 'ПОКУПАТЕЛЬ ИЩЕТ', _PAD_X + 28, cursor, font, _RED)
    return cursor + _text_size(draw, 'ПОКУПАТЕЛЬ ИЩЕТ', font)[1] + 28


def _draw_vehicle_title(draw, content: BannerContent, cursor: int) -> int:
    title = ' '.join(bit for bit in (content.brand, content.model) if bit).upper()
    max_width = _WIDTH - _PAD_X * 2
    font = _font(64, bold=True)
    lines = _wrap(draw, title, font, max_width, 2) or ['']
    if lines and _text_size(draw, lines[0], font)[0] > max_width:
        font = _font(46, bold=True)
        lines = _wrap(draw, title, font, max_width, 2)
    for line in lines:
        cursor = _draw_left(draw, line, _PAD_X, cursor, font, _INK) + 6
    if content.year:
        year_font = _font(36, bold=False)
        cursor = _draw_left(draw, content.year, _PAD_X, cursor + 4, year_font, _MUTED) + 8
    return cursor + 12


def _paste_vehicle(image: Image.Image, content: BannerContent, cursor: int) -> int:
    if content.vehicle_image is None:
        return cursor
    box_height = 210 if content.vehicle_is_generic else 280
    box = (_PAD_X, cursor, _WIDTH - _PAD_X, cursor + box_height)
    fitted = content.vehicle_image.copy()
    fitted.thumbnail((box[2] - box[0] - 24, box_height - 24), Image.Resampling.LANCZOS)
    paste_x = box[0] + ((_WIDTH - _PAD_X * 2) - fitted.width) // 2
    paste_y = box[1] + (box_height - fitted.height) // 2
    canvas = Image.new('RGB', (_WIDTH - _PAD_X * 2, box_height), (232, 234, 238))
    canvas.paste(fitted, (paste_x - box[0], paste_y - box[1]))
    image.paste(canvas, (_PAD_X, cursor))
    return cursor + box_height + 24


def _draw_parts(draw, image, content: BannerContent, cursor: int, bottom_limit: int) -> int:
    if content.part_images:
        thumb = 96
        x = _PAD_X
        for part_image in content.part_images[:3]:
            if cursor + thumb > _HEIGHT - _FOOTER_HEIGHT - _CTA_RESERVE:
                break
            fitted = part_image.copy()
            fitted.thumbnail((thumb, thumb), Image.Resampling.LANCZOS)
            frame = Image.new('RGB', (thumb, thumb), _WHITE)
            frame.paste(fitted, ((thumb - fitted.width) // 2, (thumb - fitted.height) // 2))
            image.paste(frame, (x, cursor))
            x += thumb + 16
        cursor += thumb + 16
    font = _font(36, bold=True)
    max_width = _WIDTH - _PAD_X * 2
    for part in content.parts:
        if cursor > _HEIGHT - _FOOTER_HEIGHT - _CTA_RESERVE:
            break
        lines = _wrap(draw, part, font, max_width, 2)
        for line in lines:
            cursor = _draw_left(draw, line, _PAD_X, cursor, font, _INK) + 4
        cursor += 8
    if content.hidden_part_count:
        more_font = _font(30, bold=False)
        label = _more_parts_label(content.hidden_part_count)
        cursor = _draw_left(draw, label, _PAD_X, cursor, more_font, _MUTED) + 8
    return cursor + 8


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


def _draw_meta(draw, content: BannerContent, cursor: int) -> int:
    if content.city:
        font = _font(32, bold=False)
        cursor = _draw_left(draw, content.city, _PAD_X, cursor, font, _INK) + 10
    if content.request_number:
        font = _font(28, bold=True)
        cursor = _draw_left(
            draw,
            f'Заявка №{content.request_number}',
            _PAD_X,
            cursor,
            font,
            _RED,
        ) + 16
    return cursor


def _draw_cta_stack(draw, cursor: int, bottom_limit: int) -> int:
    seller_lines = ('ПРОДАЁТЕ ЗАПЧАСТИ?', 'ПОЛУЧАЙТЕ ЗАЯВКИ', 'НА ZPT.KZ')
    buyer_lines = ('ИЩЕТЕ ЗАПЧАСТЬ?', 'ОСТАВЬТЕ ЗАЯВКУ', 'НА ZPT.KZ')
    seller_height = _cta_height(draw, seller_lines, 30)
    buyer_height = _cta_height(draw, buyer_lines, 26)
    needed = seller_height + buyer_height + 16
    cursor = max(cursor, bottom_limit - needed)
    if cursor + needed > bottom_limit:
        cursor = bottom_limit - needed
    cursor = _draw_cta(draw, cursor, seller_lines, _RED, 30)
    cursor += 14
    cursor = _draw_cta(draw, cursor, buyer_lines, _BLUE, 26)
    return cursor


def _cta_height(draw, lines, size: int) -> int:
    font = _font(size, bold=True)
    line_height = _text_size(draw, 'Аy', font)[1]
    return 28 + len(lines) * line_height + (len(lines) - 1) * 4 + 22


def _draw_cta(draw, cursor: int, lines: tuple[str, ...], color, size: int) -> int:
    font = _font(size, bold=True)
    height = _cta_height(draw, lines, size)
    left = _PAD_X
    right = _WIDTH - _PAD_X
    bottom = min(cursor + height, _HEIGHT - _FOOTER_HEIGHT - 16)
    draw.rounded_rectangle((left, cursor, right, bottom), radius=18, fill=_WHITE, outline=_LINE, width=2)
    draw.rectangle((left, cursor + 12, left + 8, bottom - 12), fill=color)
    text_y = cursor + 18
    for line in lines:
        text_y = _draw_left(draw, line, left + 28, text_y, font, color) + 4
    return bottom


def _draw_footer(draw) -> None:
    top = _HEIGHT - _FOOTER_HEIGHT
    draw.rectangle((0, top, _WIDTH, _HEIGHT), fill=_FOOTER)
    brand_font = _font(32, bold=True)
    tag_font = _font(22, bold=False)
    brand = 'ZPT.KZ'
    tag = 'Маркетплейс автозапчастей Казахстана'
    brand_w, brand_h = _text_size(draw, brand, brand_font)
    tag_w, tag_h = _text_size(draw, tag, tag_font)
    draw.text(((_WIDTH - brand_w) // 2, top + 24), brand, font=brand_font, fill=_WHITE)
    draw.text(((_WIDTH - tag_w) // 2, top + 24 + brand_h + 8), tag, font=tag_font, fill=(210, 214, 220))
