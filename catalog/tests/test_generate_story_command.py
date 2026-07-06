from io import StringIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from catalog.image_generator import (
    InstagramStoryGenerationError,
    instagram_story_exists,
    try_generate_instagram_story,
)
from core.models import Request


class TryGenerateInstagramStoryTests(TestCase):
    def setUp(self):
        self._media_tmp = TemporaryDirectory()
        self.addCleanup(self._media_tmp.cleanup)
        self._settings_override = self.settings(MEDIA_ROOT=self._media_tmp.name)
        self._settings_override.enable()

        self.request = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Camry',
            category='Тормоза',
            city='Алматы',
            phone='77001112233',
            status='sent',
        )

    @patch('catalog.instagram_api.try_publish_story_to_instagram')
    def test_try_generate_returns_path_on_success(self, publish_mock):
        publish_mock.return_value = 'media_123'
        output_path = try_generate_instagram_story(self.request)
        self.assertIsNotNone(output_path)
        self.assertTrue(output_path.is_file())
        publish_mock.assert_called_once()

    @patch('catalog.image_generator.generate_instagram_story')
    def test_try_generate_swallows_generation_error(self, generate_mock):
        generate_mock.side_effect = InstagramStoryGenerationError('boom')
        result = try_generate_instagram_story(self.request)
        self.assertIsNone(result)

    @patch('catalog.instagram_api.try_publish_story_to_instagram', side_effect=RuntimeError('api down'))
    def test_try_generate_returns_image_when_publish_fails(self, publish_mock):
        output_path = try_generate_instagram_story(self.request)
        self.assertIsNotNone(output_path)
        self.assertTrue(output_path.is_file())


class GenerateStoryCommandTests(TestCase):
    def setUp(self):
        self._media_tmp = TemporaryDirectory()
        self.addCleanup(self._media_tmp.cleanup)
        self._settings_override = self.settings(MEDIA_ROOT=self._media_tmp.name)
        self._settings_override.enable()

        self.requests = [
            Request.objects.create(
                transport_type='car',
                brand='Toyota',
                model=f'Model {index}',
                category='Тормоза',
                city='Алматы',
                phone=f'7700111223{index}',
                status='sent',
            )
            for index in range(3)
        ]

    def test_command_generates_for_specific_request_id(self):
        stdout = StringIO()
        target = self.requests[1]

        call_command('generate_story', request_id=target.pk, stdout=stdout)

        self.assertTrue(instagram_story_exists(target.pk))
        self.assertIn(f'Заявка #{target.pk}', stdout.getvalue())

    def test_command_skips_existing_story_in_batch_mode(self):
        from catalog.image_generator import generate_instagram_story

        generate_instagram_story(self.requests[0])

        stdout = StringIO()
        call_command('generate_story', stdout=stdout)
        output = stdout.getvalue()

        self.assertIn('Пропуск', output)
        self.assertIn(f'#{self.requests[0].pk}', output)

    @patch('catalog.management.commands.generate_story.generate_instagram_story')
    def test_command_reports_generation_error(self, generate_mock):
        generate_mock.side_effect = InstagramStoryGenerationError('disk full')

        stdout = StringIO()
        call_command('generate_story', request_id=self.requests[0].pk, stdout=stdout)

        self.assertIn('Ошибка', stdout.getvalue())

    def test_command_fails_for_missing_request(self):
        with self.assertRaises(Exception):
            call_command('generate_story', request_id=999999)
