import json
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import Client, TestCase, override_settings

from catalog.image_generator import (
    InstagramStoryGenerationError,
    build_publication_caption,
    generate_instagram_story,
)
from catalog.instagram_service import (
    is_publication_stuck_publishing,
    mark_stuck_instagram_publication_failed,
    process_instagram_publication_for_request,
    process_queued_instagram_publications,
    publish_instagram_publication,
    schedule_instagram_publication_for_request,
)
from core.instagram_sanitize import (
    build_instagram_part_display,
    build_instagram_part_text,
    build_instagram_seller_search_text,
    is_garbage_text,
    is_junk_only_description,
    sanitize_description,
)
from core.models import InstagramPublication, Request
from django.utils import timezone


INSTAGRAM_TEST_PHONES = ('77011910000', '77713607040')


@override_settings(INSTAGRAM_PUBLISH_MODE='OFF')
class InstagramPublishModeOffTests(TestCase):
    def setUp(self):
        self._media_tmp = TemporaryDirectory()
        self.addCleanup(self._media_tmp.cleanup)
        self.settings_override = self.settings(MEDIA_ROOT=self._media_tmp.name)
        self.settings_override.enable()
        self.request = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Camry',
            category='Тормоза',
            city='Алматы',
            phone='77001112233',
            status='sent',
        )

    def test_off_mode_creates_nothing(self):
        result = process_instagram_publication_for_request(self.request.pk)
        self.assertIsNone(result)
        self.assertFalse(
            InstagramPublication.objects.filter(request=self.request).exists()
        )


@override_settings(INSTAGRAM_PUBLISH_MODE='TEST')
class InstagramPublishModeTestTests(TestCase):
    def setUp(self):
        self._media_tmp = TemporaryDirectory()
        self.addCleanup(self._media_tmp.cleanup)
        self.settings_override = self.settings(MEDIA_ROOT=self._media_tmp.name)
        self.settings_override.enable()
        self.request = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Camry',
            category='Тормоза',
            description='Нужны колодки',
            city='Алматы',
            phone='77001112233',
            status='sent',
        )

    def test_test_mode_creates_draft(self):
        publication = process_instagram_publication_for_request(self.request.pk)
        self.assertIsNotNone(publication)
        publication.refresh_from_db()
        self.assertEqual(publication.status, InstagramPublication.STATUS_DRAFT)
        self.assertTrue(publication.image.name)
        self.assertIn('АВТО:', publication.caption)
        self.assertIn('Город покупателя:', publication.caption)

    def test_duplicate_publication_is_not_created(self):
        first = process_instagram_publication_for_request(self.request.pk)
        second = process_instagram_publication_for_request(self.request.pk)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            InstagramPublication.objects.filter(request=self.request).count(),
            1,
        )


class InstagramSanitizerTests(TestCase):
    def test_sanitize_removes_phone_email_vin_and_links(self):
        raw = (
            'Нужен фильтр VIN 1HGCM82633A004352 звоните +7 700 111 2233 '
            'mail@test.kz https://secret.shop/details'
        )
        cleaned = sanitize_description(raw)
        self.assertNotIn('77001112233', cleaned.replace(' ', ''))
        self.assertNotIn('7001112233', cleaned.replace(' ', ''))
        self.assertNotIn('mail@test.kz', cleaned)
        self.assertNotIn('https://', cleaned)
        self.assertNotIn('1HGCM82633A004352', cleaned)
        self.assertIn('фильтр', cleaned)

    def test_junk_fragment_not_shown_in_part_text(self):
        display = build_instagram_part_display(category='Двигатель', description='.ждлорпавы')
        self.assertEqual(display.detail, 'Двигатель')
        self.assertNotIn('ждлорпавы', display.detail)

    def test_raw_description_not_used_on_image_caption(self):
        request = Request.objects.create(
            transport_type='car',
            brand='Kia',
            model='Rio',
            category='Кузов',
            description='Позвоните 77001112233 mail@test.kz',
            city='Астана',
            phone='77009998877',
            status='sent',
        )
        with TemporaryDirectory() as media_root:
            with self.settings(MEDIA_ROOT=media_root):
                _path, caption = generate_instagram_story(request)
        self.assertNotIn('77001112233', caption)
        self.assertNotIn('mail@test.kz', caption)


@override_settings(
    INSTAGRAM_PUBLISH_MODE='LIVE',
    INSTAGRAM_ACCOUNT_ID='17841400000000000',
    INSTAGRAM_ACCESS_TOKEN='test-token',
)
class InstagramLivePublishTests(TestCase):
    def setUp(self):
        self._media_tmp = TemporaryDirectory()
        self.addCleanup(self._media_tmp.cleanup)
        self.settings_override = self.settings(MEDIA_ROOT=self._media_tmp.name)
        self.settings_override.enable()
        self.request = Request.objects.create(
            transport_type='car',
            brand='Hyundai',
            model='Sonata',
            category='Двигатель',
            city='Алматы',
            phone='77001112233',
            status='sent',
        )

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_live_mode_queues_publication_for_processing(self, publish_mock):
        publication = process_instagram_publication_for_request(self.request.pk)
        publication.refresh_from_db()

        self.assertEqual(publication.status, InstagramPublication.STATUS_QUEUED)
        publish_mock.assert_not_called()

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_published_is_not_republished(self, publish_mock):
        publication = process_instagram_publication_for_request(self.request.pk)
        publication.status = InstagramPublication.STATUS_PUBLISHED
        publication.instagram_media_id = 'media_1'
        publication.save(update_fields=['status', 'instagram_media_id'])
        publish_mock.reset_mock()

        publish_instagram_publication(publication)
        publish_mock.assert_not_called()

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_publish_failure_saves_image_url_validation_error(self, publish_mock):
        from catalog.instagram_api import InstagramPublishError

        publish_mock.side_effect = InstagramPublishError(
            'image_url недоступен для Meta API: HTTP 404 '
            '(https://zpt.kz/products/instagram_stories/missing.jpg)'
        )
        publication = process_instagram_publication_for_request(self.request.pk)
        publication.status = InstagramPublication.STATUS_QUEUED
        publication.save(update_fields=['status'])
        publish_mock.reset_mock()

        publish_instagram_publication(publication)
        publication.refresh_from_db()

        self.assertEqual(publication.status, InstagramPublication.STATUS_FAILED)
        self.assertIn('HTTP 404', publication.error_message)
        self.assertIn('https://zpt.kz/products/', publication.error_message)
        self.assertIsNone(publication.publishing_started_at)

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_publish_failure_does_not_leave_publishing_status(self, publish_mock):
        from catalog.instagram_api import InstagramPublishError

        publish_mock.side_effect = InstagramPublishError('Meta API down')
        publication = process_instagram_publication_for_request(self.request.pk)
        publication.status = InstagramPublication.STATUS_QUEUED
        publication.save(update_fields=['status'])

        publish_instagram_publication(publication)
        publication.refresh_from_db()

        self.assertEqual(publication.status, InstagramPublication.STATUS_FAILED)
        self.assertNotEqual(publication.status, InstagramPublication.STATUS_PUBLISHING)
        self.assertIsNone(publication.publishing_started_at)

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_publish_network_error_sets_failed_status(self, publish_mock):
        import requests

        publish_mock.side_effect = requests.Timeout('timeout')
        publication = process_instagram_publication_for_request(self.request.pk)
        publication.status = InstagramPublication.STATUS_QUEUED
        publication.save(update_fields=['status'])

        publish_instagram_publication(publication)
        publication.refresh_from_db()

        self.assertEqual(publication.status, InstagramPublication.STATUS_FAILED)
        self.assertIn('Сетевая ошибка Meta API', publication.error_message)

    def test_mark_stuck_publication_failed_after_five_minutes(self):
        publication = process_instagram_publication_for_request(self.request.pk)
        publication.status = InstagramPublication.STATUS_PUBLISHING
        publication.publishing_started_at = timezone.now() - timezone.timedelta(minutes=6)
        publication.save(update_fields=['status', 'publishing_started_at'])

        self.assertTrue(is_publication_stuck_publishing(publication))
        mark_stuck_instagram_publication_failed(publication)
        publication.refresh_from_db()

        self.assertEqual(publication.status, InstagramPublication.STATUS_FAILED)
        self.assertIn('5 минут', publication.error_message)

    def test_recent_publishing_is_not_marked_stuck(self):
        publication = process_instagram_publication_for_request(self.request.pk)
        publication.status = InstagramPublication.STATUS_PUBLISHING
        publication.publishing_started_at = timezone.now() - timezone.timedelta(minutes=2)
        publication.save(update_fields=['status', 'publishing_started_at'])

        self.assertFalse(is_publication_stuck_publishing(publication))
        mark_stuck_instagram_publication_failed(publication)
        publication.refresh_from_db()

        self.assertEqual(publication.status, InstagramPublication.STATUS_PUBLISHING)


class CreateRequestInstagramOnCommitTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.payload = {
            'transport_type': 'car',
            'country': 'Япония',
            'brand': 'Toyota',
            'model': 'Camry',
            'category': 'Тормоза',
            'article': '',
            'description': 'Нужны передние колодки',
            'city': 'Алматы',
            'search_scope': 'city',
            'selected_cities': [],
            'phone': '77001112233',
        }

    @override_settings(INSTAGRAM_PUBLISH_MODE='TEST')
    @patch('core.views._dispatch_due_requests')
    @patch('core.views._find_matching_sellers', return_value=([], 'none'))
    @patch('core.views._build_dispatch_queue', return_value=[])
    @patch('core.views._send_buyer_whatsapp_notification_async')
    def test_on_commit_schedules_instagram_pipeline(
        self,
        buyer_whatsapp_mock,
        dispatch_queue_mock,
        matching_mock,
        due_mock,
    ):
        with TemporaryDirectory() as media_root:
            with self.settings(MEDIA_ROOT=media_root):
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post(
                        '/api/create-request/',
                        data=json.dumps(self.payload),
                        content_type='application/json',
                    )

        self.assertEqual(response.status_code, 200)
        request_id = response.json()['id']
        self.assertTrue(
            InstagramPublication.objects.filter(request_id=request_id).exists()
        )

    @override_settings(INSTAGRAM_PUBLISH_MODE='TEST')
    @patch('core.views._dispatch_due_requests')
    @patch('core.views._find_matching_sellers', return_value=([], 'none'))
    @patch('core.views._build_dispatch_queue', return_value=[])
    @patch('core.views._send_buyer_whatsapp_notification_async')
    @patch(
        'catalog.instagram_service.generate_instagram_story',
        side_effect=InstagramStoryGenerationError('render failed'),
    )
    def test_create_request_succeeds_when_instagram_generation_fails(
        self,
        generate_mock,
        buyer_whatsapp_mock,
        dispatch_queue_mock,
        matching_mock,
        due_mock,
    ):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                '/api/create-request/',
                data=json.dumps(self.payload),
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['status'], 'ok')
        self.assertTrue(body['id'])
        generate_mock.assert_called_once()

    @patch('catalog.instagram_service.process_instagram_publication_for_request')
    def test_schedule_wrapper_swallows_instagram_errors(self, process_mock):
        process_mock.side_effect = RuntimeError('instagram down')
        schedule_instagram_publication_for_request(1)


class InstagramJunkDescriptionTests(TestCase):
    def test_is_garbage_text_detects_garbage(self):
        for value in ('test', 'тест', 'qwerty', 'asdf', '123', '.ждлорпавы'):
            self.assertTrue(is_garbage_text(value), value)

    def test_is_junk_only_description_detects_garbage(self):
        for value in ('test', 'тест', 'qwerty', 'asdf', '123', 'TEST, qwerty'):
            self.assertTrue(is_junk_only_description(value), value)

    def test_is_junk_only_description_allows_real_text(self):
        self.assertFalse(is_junk_only_description(''))
        self.assertFalse(is_junk_only_description('Нужны колодки'))
        self.assertFalse(is_junk_only_description('test колодки'))

    def test_build_instagram_part_display_keeps_category_only_for_junk_description(self):
        display = build_instagram_part_display(category='Двигатель', description='.ждлорпавы')
        self.assertEqual(display.detail, 'Двигатель')
        self.assertEqual(display.category_line, '')

    def test_seller_search_scope_examples(self):
        self.assertEqual(
            build_instagram_seller_search_text(search_scope='kazakhstan'),
            'весь Казахстан',
        )
        self.assertEqual(
            build_instagram_seller_search_text(search_scope='city', city='Астана'),
            'только город покупателя',
        )
        self.assertEqual(
            build_instagram_seller_search_text(
                search_scope='custom',
                selected_cities='Алматы',
            ),
            'Алматы',
        )
        self.assertEqual(
            build_instagram_seller_search_text(
                search_scope='custom',
                selected_cities='Алматы, Астана',
            ),
            'выбранные города',
        )


@override_settings(INSTAGRAM_PUBLISH_MODE='TEST')
class InstagramTestPhonePublicationTests(TestCase):
    def setUp(self):
        self._media_tmp = TemporaryDirectory()
        self.addCleanup(self._media_tmp.cleanup)
        self.settings_override = self.settings(MEDIA_ROOT=self._media_tmp.name)
        self.settings_override.enable()

    def test_test_mode_creates_draft_for_test_phones(self):
        for phone in INSTAGRAM_TEST_PHONES:
            with self.subTest(phone=phone):
                request = Request.objects.create(
                    transport_type='car',
                    brand='Toyota',
                    model='Camry',
                    category='Тормоза',
                    city='Алматы',
                    phone=phone,
                    status='sent',
                )
                publication = process_instagram_publication_for_request(request.pk)
                self.assertIsNotNone(publication)
                publication.refresh_from_db()
                self.assertEqual(publication.status, InstagramPublication.STATUS_DRAFT)


@override_settings(
    INSTAGRAM_PUBLISH_MODE='LIVE',
    INSTAGRAM_ACCOUNT_ID='17841400000000000',
    INSTAGRAM_ACCESS_TOKEN='test-token',
)
class InstagramLiveTestPhonePublicationTests(TestCase):
    def setUp(self):
        self._media_tmp = TemporaryDirectory()
        self.addCleanup(self._media_tmp.cleanup)
        self.settings_override = self.settings(MEDIA_ROOT=self._media_tmp.name)
        self.settings_override.enable()

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_live_mode_queues_publication_for_test_phones(self, publish_mock):
        for phone in INSTAGRAM_TEST_PHONES:
            with self.subTest(phone=phone):
                request = Request.objects.create(
                    transport_type='car',
                    brand='Kia',
                    model='Rio',
                    category='Кузов',
                    city='Астана',
                    phone=phone,
                    status='sent',
                )
                publication = process_instagram_publication_for_request(request.pk)
                publication.refresh_from_db()
                self.assertEqual(publication.status, InstagramPublication.STATUS_QUEUED)
                publish_mock.assert_not_called()

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_junk_description_stays_draft_in_live_mode(self, publish_mock):
        request = Request.objects.create(
            transport_type='car',
            brand='Hyundai',
            model='Sonata',
            category='Двигатель',
            description='test',
            city='Алматы',
            phone='77001112233',
            status='sent',
        )
        publication = process_instagram_publication_for_request(request.pk)
        publication.refresh_from_db()
        self.assertEqual(publication.status, InstagramPublication.STATUS_DRAFT)
        publish_mock.assert_not_called()


@override_settings(
    INSTAGRAM_PUBLISH_MODE='LIVE',
    INSTAGRAM_ACCOUNT_ID='17841400000000000',
    INSTAGRAM_ACCESS_TOKEN='test-token',
)
class InstagramBuyerPhonePrivacyTests(TestCase):
    def setUp(self):
        self._media_tmp = TemporaryDirectory()
        self.addCleanup(self._media_tmp.cleanup)
        self.settings_override = self.settings(MEDIA_ROOT=self._media_tmp.name)
        self.settings_override.enable()

    def test_buyer_phone_not_in_story_caption_or_access_token(self):
        request = Request.objects.create(
            transport_type='car',
            brand='Mercedes-Benz',
            model='GLE',
            category='Топливная система',
            description='Нужен насос',
            city='Алматы',
            phone='77011910000',
            status='sent',
        )
        _path, caption = generate_instagram_story(request)
        self.assertNotIn(request.phone, caption)
        self.assertNotIn(str(request.access_token), caption)
        self.assertNotIn(request.phone, build_publication_caption(request))


@override_settings(
    INSTAGRAM_PUBLISH_MODE='LIVE',
    INSTAGRAM_ACCOUNT_ID='17841400000000000',
    INSTAGRAM_ACCESS_TOKEN='test-token',
)
class CreateRequestInstagramLiveNoMetaApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.payload = {
            'transport_type': 'car',
            'country': 'Япония',
            'brand': 'Toyota',
            'model': 'Camry',
            'category': 'Тормоза',
            'article': '',
            'description': 'Нужны передние колодки',
            'city': 'Алматы',
            'search_scope': 'city',
            'selected_cities': [],
            'phone': '77713607040',
        }

    @patch('catalog.instagram_service.publish_story_to_instagram')
    @patch('core.views._dispatch_due_requests')
    @patch('core.views._find_matching_sellers', return_value=([], 'none'))
    @patch('core.views._build_dispatch_queue', return_value=[])
    @patch('core.views._send_buyer_whatsapp_notification_async')
    def test_create_request_queues_instagram_without_meta_api(
        self,
        buyer_whatsapp_mock,
        dispatch_queue_mock,
        matching_mock,
        due_mock,
        publish_mock,
    ):
        with TemporaryDirectory() as media_root:
            with self.settings(MEDIA_ROOT=media_root):
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post(
                        '/api/create-request/',
                        data=json.dumps(self.payload),
                        content_type='application/json',
                    )

        self.assertEqual(response.status_code, 200)
        request_id = response.json()['id']
        publication = InstagramPublication.objects.get(request_id=request_id)
        self.assertEqual(publication.status, InstagramPublication.STATUS_QUEUED)
        publish_mock.assert_not_called()


@override_settings(
    INSTAGRAM_PUBLISH_MODE='LIVE',
    INSTAGRAM_ACCOUNT_ID='17841400000000000',
    INSTAGRAM_ACCESS_TOKEN='test-token',
)
class InstagramCronPublishesQueuedTests(TestCase):
    def setUp(self):
        self._media_tmp = TemporaryDirectory()
        self.addCleanup(self._media_tmp.cleanup)
        self.settings_override = self.settings(MEDIA_ROOT=self._media_tmp.name)
        self.settings_override.enable()

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_cron_publishes_queued_publication(self, publish_mock):
        publish_mock.return_value = {
            'container_id': 'container_live',
            'media_id': 'media_live',
        }
        request = Request.objects.create(
            transport_type='car',
            brand='BMW',
            model='X5',
            category='Подвеска',
            city='Алматы',
            phone='77713607040',
            status='sent',
        )
        publication = process_instagram_publication_for_request(request.pk)
        publication.refresh_from_db()
        self.assertEqual(publication.status, InstagramPublication.STATUS_QUEUED)
        publish_mock.reset_mock()

        stats = process_queued_instagram_publications()
        publication.refresh_from_db()

        self.assertEqual(stats['published'], 1)
        self.assertEqual(publication.status, InstagramPublication.STATUS_PUBLISHED)
        publish_mock.assert_called_once()
