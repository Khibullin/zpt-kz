from io import StringIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from catalog.instagram_api import InstagramPublishError, InstagramTemporaryImageUrlError
from catalog.instagram_service import (
    process_queued_instagram_publications,
    process_instagram_publication_for_request,
    publish_instagram_publication,
)
from core.admin import (
    InstagramPublicationAdmin,
    publish_instagram_publications,
    retry_instagram_publications,
)
from core.models import InstagramPublication, Request


@override_settings(
    INSTAGRAM_PUBLISH_MODE='TEST',
    INSTAGRAM_ACCOUNT_ID='17841400000000000',
    INSTAGRAM_ACCESS_TOKEN='test-token',
)
class InstagramAdminQueueTests(TestCase):
    def setUp(self):
        self._media_tmp = TemporaryDirectory()
        self.addCleanup(self._media_tmp.cleanup)
        self.settings_override = self.settings(MEDIA_ROOT=self._media_tmp.name)
        self.settings_override.enable()

        self.request_obj = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Camry',
            category='Тормоза',
            city='Алматы',
            phone='77001112233',
            status='sent',
        )
        self.publication = process_instagram_publication_for_request(self.request_obj.pk)
        self.publication.status = InstagramPublication.STATUS_DRAFT
        self.publication.save(update_fields=['status'])

        self.factory = RequestFactory()
        self.admin_request = self.factory.post('/admin/core/instagrampublication/')
        setattr(self.admin_request, 'session', 'session')
        messages = FallbackStorage(self.admin_request)
        setattr(self.admin_request, '_messages', messages)

        self.modeladmin = InstagramPublicationAdmin(InstagramPublication, AdminSite())

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_publish_admin_action_only_queues_queued(self, publish_mock):
        queryset = InstagramPublication.objects.filter(pk=self.publication.pk)

        publish_instagram_publications(self.modeladmin, self.admin_request, queryset)
        self.publication.refresh_from_db()

        self.assertEqual(self.publication.status, InstagramPublication.STATUS_QUEUED)
        self.assertEqual(self.publication.error_message, '')
        self.assertIsNone(self.publication.publishing_started_at)
        publish_mock.assert_not_called()

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_retry_admin_action_does_not_call_meta_api(self, publish_mock):
        self.publication.status = InstagramPublication.STATUS_FAILED
        self.publication.error_message = 'old error'
        self.publication.save(update_fields=['status', 'error_message'])

        queryset = InstagramPublication.objects.filter(pk=self.publication.pk)
        retry_instagram_publications(self.modeladmin, self.admin_request, queryset)
        self.publication.refresh_from_db()

        self.assertEqual(self.publication.status, InstagramPublication.STATUS_QUEUED)
        self.assertEqual(self.publication.error_message, '')
        publish_mock.assert_not_called()


@override_settings(
    INSTAGRAM_PUBLISH_MODE='TEST',
    INSTAGRAM_ACCOUNT_ID='17841400000000000',
    INSTAGRAM_ACCESS_TOKEN='test-token',
)
class InstagramManagementCommandTests(TestCase):
    def setUp(self):
        self._media_tmp = TemporaryDirectory()
        self.addCleanup(self._media_tmp.cleanup)
        self.settings_override = self.settings(MEDIA_ROOT=self._media_tmp.name)
        self.settings_override.enable()

        self.request_obj = Request.objects.create(
            transport_type='car',
            brand='Kia',
            model='Rio',
            category='Кузов',
            city='Астана',
            phone='77009998877',
            status='sent',
        )
        self.publication = process_instagram_publication_for_request(self.request_obj.pk)
        self.publication.status = InstagramPublication.STATUS_QUEUED
        self.publication.save(update_fields=['status'])

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_management_command_processes_queued_publication(self, publish_mock):
        publish_mock.return_value = {
            'container_id': 'container_99',
            'media_id': 'media_99',
        }

        stats = process_queued_instagram_publications()
        self.publication.refresh_from_db()

        self.assertEqual(stats['processed'], 1)
        self.assertEqual(stats['published'], 1)
        self.assertEqual(self.publication.status, InstagramPublication.STATUS_PUBLISHED)
        publish_mock.assert_called_once()
        self.assertTrue(publish_mock.call_args.kwargs.get('validate_image_url'))

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_approved_status_is_not_processed_by_cron(self, publish_mock):
        self.publication.status = InstagramPublication.STATUS_APPROVED
        self.publication.save(update_fields=['status'])

        stats = process_queued_instagram_publications()
        self.publication.refresh_from_db()

        self.assertEqual(stats['processed'], 0)
        self.assertEqual(self.publication.status, InstagramPublication.STATUS_APPROVED)
        publish_mock.assert_not_called()

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_management_command_handles_validation_timeout(self, publish_mock):
        publish_mock.side_effect = InstagramPublishError(
            'Не удалось проверить публичный URL изображения: timeout'
        )

        stats = process_queued_instagram_publications()
        self.publication.refresh_from_db()

        self.assertEqual(stats['failed'], 1)
        self.assertEqual(self.publication.status, InstagramPublication.STATUS_FAILED)
        self.assertIn('timeout', self.publication.error_message)
        self.assertIsNone(self.publication.publishing_started_at)

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_management_command_callable_via_manage_py(self, publish_mock):
        publish_mock.return_value = {
            'container_id': 'container_1',
            'media_id': 'media_1',
        }

        stdout = StringIO()
        call_command('process_instagram_publications', stdout=stdout)
        output = stdout.getvalue()

        self.assertIn('InstagramPublication total=', output)
        self.assertIn('queued (В очереди):', output)
        self.assertIn('Recent publications:', output)
        self.assertIn('published=1', output)
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.status, InstagramPublication.STATUS_PUBLISHED)

    @patch('catalog.instagram_service.publish_story_to_instagram', side_effect=RuntimeError('boom'))
    def test_publication_not_left_publishing_after_exception(self, publish_mock):
        publish_instagram_publication(
            self.publication,
            validate_image_url=False,
            source='management',
        )
        self.publication.refresh_from_db()

        self.assertEqual(self.publication.status, InstagramPublication.STATUS_FAILED)
        self.assertNotEqual(self.publication.status, InstagramPublication.STATUS_PUBLISHING)
        self.assertIsNone(self.publication.publishing_started_at)

    def test_management_command_resets_stuck_publishing(self):
        self.publication.status = InstagramPublication.STATUS_PUBLISHING
        self.publication.publishing_started_at = timezone.now() - timezone.timedelta(minutes=6)
        self.publication.save(update_fields=['status', 'publishing_started_at'])

        stats = process_queued_instagram_publications()
        self.publication.refresh_from_db()

        self.assertEqual(stats['stuck_reset'], 1)
        self.assertEqual(self.publication.status, InstagramPublication.STATUS_FAILED)


@override_settings(
    INSTAGRAM_PUBLISH_MODE='TEST',
    INSTAGRAM_ACCOUNT_ID='17841400000000000',
    INSTAGRAM_ACCESS_TOKEN='test-token',
    PUBLIC_BASE_URL='https://zpt.kz',
    MEDIA_URL='/products/',
)
class InstagramTemporaryImageUrlRetryTests(TestCase):
    def setUp(self):
        self._media_tmp = TemporaryDirectory()
        self.addCleanup(self._media_tmp.cleanup)
        self.settings_override = self.settings(MEDIA_ROOT=self._media_tmp.name)
        self.settings_override.enable()

        self.request_obj = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Camry',
            category='Тормоза',
            city='Алматы',
            phone='77001112233',
            status='sent',
        )
        self.publication = process_instagram_publication_for_request(self.request_obj.pk)
        self.publication.status = InstagramPublication.STATUS_QUEUED
        self.publication.save(update_fields=['status'])

    def _temporary_502_error(self):
        return InstagramTemporaryImageUrlError(
            502,
            'https://zpt.kz/products/instagram_stories/request.jpg',
        )

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_http_502_returns_publication_to_queued(self, publish_mock):
        publish_mock.side_effect = self._temporary_502_error()

        publish_instagram_publication(
            self.publication,
            validate_image_url=True,
            source='management',
        )
        self.publication.refresh_from_db()

        self.assertEqual(self.publication.status, InstagramPublication.STATUS_QUEUED)
        self.assertEqual(self.publication.retry_count, 1)
        self.assertIn('HTTP 502', self.publication.error_message)
        self.assertIn('Будет повторено позже', self.publication.error_message)
        self.assertIsNone(self.publication.publishing_started_at)
        self.assertIsNotNone(self.publication.last_attempt_at)

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_http_502_fails_after_three_attempts(self, publish_mock):
        publish_mock.side_effect = self._temporary_502_error()

        for expected_retry in (1, 2):
            publish_instagram_publication(
                self.publication,
                validate_image_url=True,
                source='management',
            )
            self.publication.refresh_from_db()
            self.assertEqual(self.publication.status, InstagramPublication.STATUS_QUEUED)
            self.assertEqual(self.publication.retry_count, expected_retry)

        publish_instagram_publication(
            self.publication,
            validate_image_url=True,
            source='management',
        )
        self.publication.refresh_from_db()

        self.assertEqual(self.publication.status, InstagramPublication.STATUS_FAILED)
        self.assertEqual(self.publication.retry_count, 3)
        self.assertIn('HTTP 502', self.publication.error_message)
        self.assertIn('Превышено число попыток', self.publication.error_message)

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_http_404_fails_immediately(self, publish_mock):
        publish_mock.side_effect = InstagramPublishError(
            'image_url недоступен для Meta API: HTTP 404 '
            '(https://zpt.kz/products/instagram_stories/missing.jpg)'
        )

        publish_instagram_publication(
            self.publication,
            validate_image_url=True,
            source='management',
        )
        self.publication.refresh_from_db()

        self.assertEqual(self.publication.status, InstagramPublication.STATUS_FAILED)
        self.assertEqual(self.publication.retry_count, 0)
        self.assertIn('HTTP 404', self.publication.error_message)

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_http_200_jpeg_publishes_successfully(self, publish_mock):
        publish_mock.return_value = {
            'container_id': 'container_ok',
            'media_id': 'media_ok',
        }

        publish_instagram_publication(
            self.publication,
            validate_image_url=True,
            source='management',
        )
        self.publication.refresh_from_db()

        self.assertEqual(self.publication.status, InstagramPublication.STATUS_PUBLISHED)
        self.assertEqual(self.publication.retry_count, 0)
        self.assertEqual(self.publication.instagram_media_id, 'media_ok')
        publish_mock.assert_called_once()
