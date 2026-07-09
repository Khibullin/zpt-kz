from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase

from catalog.image_generator import (
    InstagramStoryGenerationError,
    _format_city_line,
    _format_part_line,
    _format_vehicle_line,
    _wrap_paragraph,
    build_publication_caption,
    generate_instagram_story,
)
from core.instagram_sanitize import (
    build_instagram_geography_text,
    build_instagram_part_text,
    is_junk_only_description,
    is_junk_text_fragment,
    sanitize_description,
)
from core.models import Request
from PIL import Image, ImageDraw, ImageFont


class InstagramSanitizeHelperTests(TestCase):
    def test_is_junk_text_fragment_detects_keyboard_mash(self):
        self.assertTrue(is_junk_text_fragment('.ждлорпавы'))
        self.assertTrue(is_junk_text_fragment('qwerty'))
        self.assertFalse(is_junk_text_fragment('Двигатель'))

    def test_build_instagram_part_text_ignores_junk_description(self):
        part = build_instagram_part_text(
            category='Двигатель',
            description='.ждлорпавы',
        )
        self.assertEqual(part, 'Двигатель')

    def test_build_instagram_part_text_uses_fallback_without_data(self):
        part = build_instagram_part_text(category='', description='test')
        self.assertEqual(part, 'Запчасть по заявке')

    def test_build_instagram_geography_custom_multiple_cities(self):
        text = build_instagram_geography_text(
            search_scope='custom',
            selected_cities='Алматы, Астана',
        )
        self.assertEqual(text, 'Выбранные города')

    def test_build_instagram_geography_defaults_to_kazakhstan(self):
        self.assertEqual(
            build_instagram_geography_text(search_scope='city', city=''),
            'Казахстан',
        )


class InstagramStoryGeneratorTests(TestCase):
    def setUp(self):
        self._media_tmp = TemporaryDirectory()
        self.addCleanup(self._media_tmp.cleanup)
        self._settings_override = self.settings(MEDIA_ROOT=self._media_tmp.name)
        self._settings_override.enable()

        self.request = Request.objects.create(
            transport_type='car',
            country='Китай',
            brand='Chery',
            model='Tiggo 7',
            category='Двигатель',
            article='CH-123',
            description='Нужен масляный фильтр в хорошем состоянии',
            city='Алматы',
            phone='77001112233',
            status='new',
        )

    def test_format_vehicle_line_without_year(self):
        line = _format_vehicle_line(self.request)
        self.assertEqual(line, 'Chery Tiggo 7')

    def test_format_part_line_uses_category_and_description(self):
        line = _format_part_line(self.request)
        self.assertIn('Двигатель', line)
        self.assertIn('масляный фильтр', line)
        self.assertIn('Арт. CH-123', line)
        self.assertNotIn('77001112233', line)

    def test_format_part_line_filters_junk_description(self):
        self.request.description = '.ждлорпавы'
        line = _format_part_line(self.request)
        self.assertEqual(line, 'Двигатель · Арт. CH-123')

    def test_build_publication_caption_excludes_phone_from_description(self):
        self.request.description = 'Фильтр, звоните 77001112233'
        caption = build_publication_caption(self.request)
        self.assertNotIn('77001112233', caption)
        self.assertIn('ГЕОГРАФИЯ:', caption)

    def test_format_city_line_for_kazakhstan_scope(self):
        self.request.search_scope = 'kazakhstan'
        self.assertEqual(_format_city_line(self.request), 'Весь Казахстан')

    def test_format_city_line_for_custom_scope_single_city(self):
        self.request.search_scope = 'custom'
        self.request.selected_cities = 'Алматы'
        self.assertEqual(_format_city_line(self.request), 'Алматы')

    def test_format_city_line_for_custom_scope_multiple_cities(self):
        self.request.search_scope = 'custom'
        self.request.selected_cities = 'Алматы, Астана'
        self.assertEqual(_format_city_line(self.request), 'Выбранные города')

    def test_wrap_paragraph_truncates_very_long_text(self):
        draw = ImageDraw.Draw(Image.new('RGB', (1080, 1920)))
        font = ImageFont.load_default()
        long_text = 'слово ' * 200
        lines = _wrap_paragraph(draw, long_text, font, max_width=80, max_lines=3)
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[-1].endswith('\u2026'))

    def test_generate_instagram_story_creates_jpeg(self):
        output_path, caption = generate_instagram_story(self.request)

        self.assertTrue(output_path.is_file())
        self.assertEqual(output_path.suffix, '.jpg')
        self.assertIn('instagram_stories', output_path.as_posix())
        self.assertIn(str(self.request.access_token), output_path.name)
        self.assertIn('АВТО:', caption)
        self.assertIn('ГЕОГРАФИЯ:', caption)

        with Image.open(output_path) as image:
            self.assertEqual(image.size, (1080, 1920))
            self.assertEqual(image.mode, 'RGB')
            self.assertEqual(image.format, 'JPEG')

    def test_generate_instagram_story_uses_fallback_background(self):
        with patch('catalog.image_generator._background_path') as bg_mock:
            bg_mock.return_value = Path('/nonexistent/instagram_bg.png')
            output_path, _caption = generate_instagram_story(self.request)

        self.assertTrue(output_path.is_file())

    def test_generate_instagram_story_requires_saved_request(self):
        unsaved = Request(
            transport_type='car',
            phone='77001112233',
        )
        with self.assertRaises(InstagramStoryGenerationError):
            generate_instagram_story(unsaved)

    def test_generate_instagram_story_raises_on_save_failure(self):
        with patch.object(Image.Image, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(InstagramStoryGenerationError):
                generate_instagram_story(self.request)

    def test_is_junk_only_description_helper(self):
        self.assertTrue(is_junk_only_description('qwerty'))
        self.assertFalse(is_junk_only_description('Нужен масляный фильтр'))
