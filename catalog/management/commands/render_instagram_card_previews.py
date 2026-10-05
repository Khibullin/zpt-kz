"""Локальные превью карточек. В Instagram ничего не отправляет."""

from pathlib import Path

from django.core.management.base import BaseCommand
from PIL import Image

from catalog.instagram_card import InstagramCardContent, render_instagram_card


SAMPLES = (
    (
        '01-ordinary',
        InstagramCardContent(
            vehicle='Toyota Camry',
            part='Передние тормозные колодки',
            location='Алматы',
            request_number='№ 481',
        ),
    ),
    (
        '02-long-part',
        InstagramCardContent(
            vehicle='Hyundai Sonata',
            part='Передний левый блок управления климат-контролем в сборе с панелью и проводкой',
            location='Астана',
            request_number='№ 482',
        ),
    ),
    (
        '03-long-vehicle',
        InstagramCardContent(
            vehicle='Mercedes-Benz GLE Coupe 400 d 4MATIC 2019',
            part='Фара передняя левая',
            location='Караганда',
            request_number='№ 483',
        ),
    ),
    (
        '04-incomplete',
        InstagramCardContent(
            vehicle='Kia Rio',
            part='Фара',
            location='',
            request_number='',
        ),
    ),
    (
        '05-mixed-script',
        InstagramCardContent(
            vehicle='Lexus ES 350',
            part='Блок климат-контроля HVAC',
            location='Шымкент • По Казахстану',
            request_number='№ 485',
        ),
    ),
    (
        '06-custom-cities',
        InstagramCardContent(
            vehicle='BYD Song Plus',
            part='Радиатор кондиционера',
            location='Шымкент • Алматы, Астана',
            request_number='№ 486',
        ),
    ),
)


class Command(BaseCommand):
    help = 'Сохраняет локальные превью ленты, сторис и сетки профиля. Instagram не вызывается.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='var/instagram-card-previews',
            help='Каталог для JPEG-превью',
        )

    def handle(self, *args, **options):
        output = Path(options['output'])
        output.mkdir(parents=True, exist_ok=True)
        feed_images = []
        for name, content in SAMPLES:
            feed = render_instagram_card(content, kind='feed')
            story = render_instagram_card(content, kind='story')
            feed_path = output / f'{name}-feed.jpg'
            story_path = output / f'{name}-story.jpg'
            phone_path = output / f'{name}-feed-phone.jpg'
            feed.image.save(feed_path, format='JPEG', quality=90, optimize=True)
            story.image.save(story_path, format='JPEG', quality=90, optimize=True)
            phone = feed.image.resize((390, 488), Image.Resampling.LANCZOS)
            phone.save(phone_path, format='JPEG', quality=90, optimize=True)
            feed_images.append(feed.image)
            self.stdout.write(f'{feed_path.name} headline={feed.headline_size} vehicle={feed.vehicle_size} part={feed.part_size}')

        _save_profile_grid(feed_images, output / '07-profile-grid.jpg')
        _save_square_grid(feed_images, output / '08-profile-squares.jpg')
        self.stdout.write(self.style.SUCCESS(f'Превью сохранены в {output}'))


def _save_profile_grid(images: list[Image.Image], path: Path) -> None:
    columns = 3
    thumb_w = 360
    gap = 16
    selected = images[:6]
    thumbs = [
        image.resize((thumb_w, int(thumb_w * image.height / image.width)), Image.Resampling.LANCZOS)
        for image in selected
    ]
    row_h = thumbs[0].height
    rows = (len(thumbs) + columns - 1) // columns
    width = columns * thumb_w + (columns + 1) * gap
    height = rows * row_h + (rows + 1) * gap
    canvas = Image.new('RGB', (width, height), (255, 255, 255))
    for index, thumb in enumerate(thumbs):
        column = index % columns
        row = index // columns
        x = gap + column * (thumb_w + gap)
        y = gap + row * (row_h + gap)
        canvas.paste(thumb, (x, y))
    canvas.save(path, format='JPEG', quality=90, optimize=True)


def _save_square_grid(images: list[Image.Image], path: Path) -> None:
    """Центральный квадрат, как сетка профиля Instagram обрезает пост 4:5."""
    columns = 3
    thumb = 360
    gap = 8
    selected = images[:6]
    squares = []
    for image in selected:
        side = image.width
        top = max(0, (image.height - side) // 2)
        crop = image.crop((0, top, side, top + side))
        squares.append(crop.resize((thumb, thumb), Image.Resampling.LANCZOS))
    rows = 2
    width = columns * thumb + (columns + 1) * gap
    height = rows * thumb + (rows + 1) * gap
    canvas = Image.new('RGB', (width, height), (255, 255, 255))
    for index, square in enumerate(squares):
        column = index % columns
        row = index // columns
        canvas.paste(square, (gap + column * (thumb + gap), gap + row * (thumb + gap)))
    canvas.save(path, format='JPEG', quality=90, optimize=True)
