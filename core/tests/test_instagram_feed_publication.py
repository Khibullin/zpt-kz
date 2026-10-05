from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from catalog.instagram_api import InstagramAmbiguousPublishError, InstagramPublishError, InstagramQuotaError
from catalog.instagram_service import (
    process_instagram_publication_for_request,
    process_queued_instagram_publications,
    publish_instagram_publication,
)
from core.models import InstagramPublication, Request


def _request(**overrides):
    payload = {
        'transport_type': 'car',
        'brand': 'Toyota',
        'model': 'Camry',
        'category': 'Тормоза',
        'description': 'Передние колодки',
        'city': 'Алматы',
        'search_scope': 'city',
        'phone': '77001112233',
        'status': 'sent',
    }
    payload.update(overrides)
    return Request.objects.create(**payload)


@override_settings(
    INSTAGRAM_PUBLISH_MODE='LIVE',
    INSTAGRAM_FEED_PUBLISH_ENABLED=True,
    INSTAGRAM_ACCOUNT_ID='17841400000000000',
    INSTAGRAM_ACCESS_TOKEN='test-token',
    INSTAGRAM_DAILY_PUBLISH_LIMIT=50,
)
class InstagramFeedPublicationTests(TestCase):
    def setUp(self):
        self._media_tmp = TemporaryDirectory()
        self.addCleanup(self._media_tmp.cleanup)
        self.settings_override = self.settings(MEDIA_ROOT=self._media_tmp.name)
        self.settings_override.enable()

    def _placements(self, request):
        return {
            item.placement: item
            for item in InstagramPublication.objects.filter(request=request)
        }

    @patch('catalog.instagram_service.publish_feed_to_instagram')
    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_new_request_creates_one_feed_post_and_repeat_does_not_duplicate(
        self,
        story_mock,
        feed_mock,
    ):
        story_mock.return_value = {'container_id': 'c-story', 'media_id': 'm-story'}
        feed_mock.return_value = {'container_id': 'c-feed', 'media_id': 'm-feed'}
        request = _request()

        first = process_instagram_publication_for_request(request.pk)
        second = process_instagram_publication_for_request(request.pk)
        placements = self._placements(request)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(len(placements), 2)
        self.assertEqual(placements['story'].status, InstagramPublication.STATUS_QUEUED)
        self.assertEqual(placements['feed'].status, InstagramPublication.STATUS_QUEUED)

        process_queued_instagram_publications()
        process_queued_instagram_publications()
        placements = self._placements(request)

        self.assertEqual(placements['feed'].status, InstagramPublication.STATUS_PUBLISHED)
        self.assertEqual(placements['feed'].instagram_media_id, 'm-feed')
        self.assertEqual(placements['story'].instagram_media_id, 'm-story')
        self.assertEqual(feed_mock.call_count, 1)
        self.assertEqual(story_mock.call_count, 1)

    @patch('catalog.instagram_service.publish_feed_to_instagram')
    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_feed_and_story_failures_are_independent(self, story_mock, feed_mock):
        story_mock.side_effect = InstagramPublishError('story down')
        feed_mock.return_value = {'container_id': 'c-feed', 'media_id': 'm-feed'}
        request = _request(phone='77001112234')
        process_instagram_publication_for_request(request.pk)

        process_queued_instagram_publications()
        placements = self._placements(request)

        self.assertEqual(placements['story'].status, InstagramPublication.STATUS_FAILED)
        self.assertEqual(placements['feed'].status, InstagramPublication.STATUS_PUBLISHED)
        self.assertEqual(story_mock.call_count, 1)
        self.assertEqual(feed_mock.call_count, 1)

        story_mock.reset_mock()
        feed_mock.reset_mock()
        process_queued_instagram_publications()
        story_mock.assert_not_called()
        feed_mock.assert_not_called()

    @patch('catalog.instagram_service.publish_feed_to_instagram')
    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_story_is_not_republished_when_feed_fails(self, story_mock, feed_mock):
        story_mock.return_value = {'container_id': 'c-story', 'media_id': 'm-story'}
        feed_mock.side_effect = InstagramPublishError('feed down')
        request = _request(phone='77001112235')
        process_instagram_publication_for_request(request.pk)
        process_queued_instagram_publications()
        placements = self._placements(request)

        self.assertEqual(placements['story'].status, InstagramPublication.STATUS_PUBLISHED)
        self.assertEqual(placements['feed'].status, InstagramPublication.STATUS_FAILED)
        self.assertEqual(story_mock.call_count, 1)

    @patch('catalog.instagram_service.inspect_instagram_container')
    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_ambiguous_response_is_not_retried_blindly(self, story_mock, inspect_mock):
        story_mock.side_effect = InstagramAmbiguousPublishError('ответ не получен')
        inspect_mock.return_value = {'status_code': '', 'media_id': ''}
        request = _request(phone='77001112236')
        with self.settings(INSTAGRAM_FEED_PUBLISH_ENABLED=False):
            publication = process_instagram_publication_for_request(request.pk)
            publish_instagram_publication(publication)
            publication.refresh_from_db()
            self.assertEqual(publication.status, InstagramPublication.STATUS_NEEDS_REVIEW)
            publish_instagram_publication(publication)
        self.assertEqual(story_mock.call_count, 1)

    @patch('catalog.instagram_service.inspect_instagram_container')
    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_ambiguous_response_uses_confirmed_media_id(self, story_mock, inspect_mock):
        def remember_and_fail(*_args, **kwargs):
            kwargs['on_container_created']('container-known')
            raise InstagramAmbiguousPublishError('ответ не получен')

        story_mock.side_effect = remember_and_fail
        inspect_mock.return_value = {'status_code': 'PUBLISHED', 'media_id': 'media-confirmed'}
        request = _request(phone='77001112237')
        with self.settings(INSTAGRAM_FEED_PUBLISH_ENABLED=False):
            publication = process_instagram_publication_for_request(request.pk)
            publish_instagram_publication(publication)
        publication.refresh_from_db()
        self.assertEqual(publication.status, InstagramPublication.STATUS_PUBLISHED)
        self.assertEqual(publication.instagram_media_id, 'media-confirmed')
        inspect_mock.assert_called_once()

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_quota_stays_queued_and_is_not_sent_again_before_delay(self, story_mock):
        story_mock.side_effect = InstagramQuotaError('limit')
        request = _request(phone='77001112238')
        with self.settings(INSTAGRAM_FEED_PUBLISH_ENABLED=False):
            publication = process_instagram_publication_for_request(request.pk)
            publish_instagram_publication(publication)
            publication.refresh_from_db()
            self.assertEqual(publication.status, InstagramPublication.STATUS_QUEUED)
            self.assertIsNotNone(publication.next_attempt_at)
            self.assertGreater(publication.next_attempt_at, timezone.now())
            stats = process_queued_instagram_publications()
        self.assertEqual(stats['published'], 0)
        self.assertEqual(story_mock.call_count, 1)

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_rejected_request_is_not_published(self, story_mock):
        request = _request(phone='77001112239', status='rejected')
        with self.settings(INSTAGRAM_FEED_PUBLISH_ENABLED=False):
            publication = process_instagram_publication_for_request(request.pk)
            self.assertEqual(publication.status, InstagramPublication.STATUS_DRAFT)
            publication.status = InstagramPublication.STATUS_QUEUED
            publication.save(update_fields=['status'])
            publish_instagram_publication(publication)
        publication.refresh_from_db()
        self.assertEqual(publication.status, InstagramPublication.STATUS_CANCELLED)
        story_mock.assert_not_called()

    @patch('catalog.instagram_service.publish_feed_to_instagram')
    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_enabling_feed_does_not_publish_existing_story_queue(self, story_mock, feed_mock):
        story_mock.return_value = {'container_id': 'c-story', 'media_id': 'm-story'}
        request = _request(phone='77001112240')
        with self.settings(INSTAGRAM_FEED_PUBLISH_ENABLED=False):
            process_instagram_publication_for_request(request.pk)
        self.assertFalse(
            InstagramPublication.objects.filter(
                request=request,
                placement=InstagramPublication.PLACEMENT_FEED,
            ).exists()
        )
        with self.settings(INSTAGRAM_FEED_PUBLISH_ENABLED=True):
            process_instagram_publication_for_request(request.pk)
            process_queued_instagram_publications()
        self.assertFalse(
            InstagramPublication.objects.filter(
                request=request,
                placement=InstagramPublication.PLACEMENT_FEED,
            ).exists()
        )
        feed_mock.assert_not_called()
        story_mock.assert_called_once()

    @patch('catalog.instagram_service.publish_feed_to_instagram')
    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_disabling_feed_does_not_disable_stories(self, story_mock, feed_mock):
        story_mock.return_value = {'container_id': 'c-story', 'media_id': 'm-story'}
        feed_mock.return_value = {'container_id': 'c-feed', 'media_id': 'm-feed'}
        request = _request(phone='77001112241')
        process_instagram_publication_for_request(request.pk)
        feed = InstagramPublication.objects.get(
            request=request,
            placement=InstagramPublication.PLACEMENT_FEED,
        )
        with self.settings(INSTAGRAM_FEED_PUBLISH_ENABLED=False):
            stats = process_queued_instagram_publications()
        feed.refresh_from_db()
        story = InstagramPublication.objects.get(
            request=request,
            placement=InstagramPublication.PLACEMENT_STORY,
        )
        self.assertEqual(stats['published'], 1)
        self.assertEqual(story.status, InstagramPublication.STATUS_PUBLISHED)
        self.assertEqual(feed.status, InstagramPublication.STATUS_QUEUED)
        feed_mock.assert_not_called()

    @patch('catalog.instagram_service.publish_story_to_instagram')
    def test_daily_limit_keeps_publication_queued(self, story_mock):
        older = _request(phone='77001112242')
        InstagramPublication.objects.create(
            request=older,
            placement=InstagramPublication.PLACEMENT_STORY,
            status=InstagramPublication.STATUS_PUBLISHED,
            published_at=timezone.now() - timedelta(hours=1),
            instagram_media_id='already',
        )
        request = _request(phone='77001112243')
        with self.settings(INSTAGRAM_FEED_PUBLISH_ENABLED=False, INSTAGRAM_DAILY_PUBLISH_LIMIT=1):
            publication = process_instagram_publication_for_request(request.pk)
            stats = process_queued_instagram_publications()
        publication.refresh_from_db()
        self.assertEqual(stats['published'], 0)
        self.assertEqual(publication.status, InstagramPublication.STATUS_QUEUED)
        self.assertIsNotNone(publication.next_attempt_at)
        story_mock.assert_not_called()

    def test_caption_and_card_do_not_include_personal_data(self):
        from catalog.image_generator import _card_content, build_publication_caption

        request = _request(
            description='Насос +7 700 111 22 33 secret@mail.test',
            vin='JTDBR32E720012345',
            phone='77009998877',
        )
        caption = build_publication_caption(request)
        card = _card_content(request)
        blob = '\n'.join([caption, card.vehicle, card.part, card.location, card.request_number])
        self.assertNotIn('77009998877', blob)
        self.assertNotIn('7001112233', blob.replace(' ', ''))
        self.assertNotIn('secret@mail.test', blob)
        self.assertNotIn('JTDBR32E720012345', blob)
        self.assertNotIn(str(request.access_token), blob)
