from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase

from catalog.image_generator import (
    InstagramStoryGenerationError,
    _format_buyer_city_line,
    _format_seller_search_line,
    _format_vehicle_line,
    _wrap_paragraph,
    build_publication_caption,
    generate_instagram_story,
)
from core.instagram_sanitize import (
    build_instagram_buyer_city_text,
    build_instagram_part_display,
    build_instagram_part_text,
    build_instagram_seller_search_text,
    clean_public_part_description,
    fix_common_part_typos,
    is_garbage_text,
    is_junk_only_description,
    normalize_instagram_part_text,
    sanitize_description,
)
from core.models import Request
from PIL import Image, ImageDraw, ImageFont


class InstagramSanitizeHelperTests(TestCase):
    def test_body_category_description_becomes_clean_detail(self):
        display = build_instagram_part_display(
            category='Кузов',
            description='Нужен передний бампер',
        )
        self.assertEqual(display.detail, 'Передний бампер')
        self.assertEqual(display.category_line, 'Категория: Кузов')

    def test_engine_category_with_junk_description(self):
        display = build_instagram_part_display(
            category='Двигатель',
            description='.ждлорпавы',
        )
        self.assertEqual(display.detail, 'Двигатель')
        self.assertEqual(display.category_line, '')

    def test_optics_category_fixes_headlight_typos(self):
        display = build_instagram_part_display(
            category='Оптика',
            description='ищу фару перднию',
        )
        self.assertEqual(display.detail, 'Передняя фара')
        self.assertEqual(display.category_line, 'Категория: Оптика')

    def test_description_only_typos_without_category(self):
        display = build_instagram_part_display(
            category='',
            description='нужен передни бампр',
        )
        self.assertEqual(display.detail, 'Передний бампер')
        self.assertEqual(display.category_line, '')

    def test_garbage_description_uses_fallback(self):
        display = build_instagram_part_display(category='', description='test')
        self.assertEqual(display.detail, 'Запчасть по заявке')

    def test_is_garbage_text_detects_keyboard_mash(self):
        self.assertTrue(is_garbage_text('.ждлорпавы'))
        self.assertTrue(is_garbage_text('qwerty'))
        self.assertFalse(is_garbage_text('Двигатель'))

    def test_normalize_instagram_part_text_strips_service_words(self):
        self.assertEqual(
            normalize_instagram_part_text('нужен передний бампер'),
            'Передний бампер',
        )

    def test_fix_common_part_typos_dictionary(self):
        self.assertEqual(fix_common_part_typos('нужын двегатель'), 'нужын двигатель')

    def test_clean_public_part_description_removes_pii(self):
        cleaned = clean_public_part_description('Фильтр 77001112233')
        self.assertNotIn('77001112233', cleaned)

    def test_seller_search_kazakhstan_scope(self):
        self.assertEqual(
            build_instagram_seller_search_text(search_scope='kazakhstan'),
            'весь Казахстан',
        )

    def test_seller_search_city_scope(self):
        self.assertEqual(
            build_instagram_seller_search_text(search_scope='city', city='Астана'),
            'только город покупателя',
        )

    def test_seller_search_custom_single_city(self):
        self.assertEqual(
            build_instagram_seller_search_text(
                search_scope='custom',
                selected_cities='Шымкент',
            ),
            'Шымкент',
        )

    def test_seller_search_custom_multiple_cities(self):
        self.assertEqual(
            build_instagram_seller_search_text(
                search_scope='custom',
                selected_cities='Алматы, Астана',
            ),
            'выбранные города',
        )

    def test_buyer_city_defaults_to_kazakhstan(self):
        self.assertEqual(build_instagram_buyer_city_text(city=''), 'Казахстан')


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

    def test_build_publication_caption_contains_new_geography_lines(self):
        self.request.search_scope = 'kazakhstan'
        caption = build_publication_caption(self.request)
        self.assertIn('Город покупателя: Алматы', caption)
        self.assertIn('Поиск продавцов: весь Казахстан', caption)
        self.assertIn('ДЕТАЛЬ:', caption)

    def test_build_publication_caption_excludes_phone_from_description(self):
        self.request.description = 'Фильтр, звоните 77001112233'
        caption = build_publication_caption(self.request)
        self.assertNotIn('77001112233', caption)

    def test_format_seller_search_line_for_city_scope(self):
        self.request.search_scope = 'city'
        self.assertEqual(
            _format_seller_search_line(self.request),
            'Поиск продавцов: только город покупателя',
        )

    def test_format_buyer_city_line(self):
        self.assertEqual(
            _format_buyer_city_line(self.request),
            'Город покупателя: Алматы',
        )

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
        self.assertIn('Город покупателя:', caption)

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

    def test_build_instagram_part_text_backward_compatible(self):
        text = build_instagram_part_text(
            category='Кузов',
            description='Нужен передний бампер',
        )
        self.assertIn('Передний бампер', text)
