from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.instagram_service import (
    _claim_instagram_publication,
    approve_instagram_publication,
    process_instagram_publication_for_request,
    publish_instagram_publication,
    queue_instagram_publication_for_processing,
)
from core.models import InstagramPublication, Request, Seller, SellerRequestAccess
from core.services.seller_request_access import create_seller_request_access


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
        'vin': 'JTDBR32E720012345',
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
class InstagramRequestPublicationV2Tests(TestCase):
    def setUp(self):
        self._media_tmp = TemporaryDirectory()
        self.addCleanup(self._media_tmp.cleanup)
        self.settings_override = self.settings(MEDIA_ROOT=self._media_tmp.name)
        self.settings_override.enable()

    def test_clear_request_still_queues_story_and_feed_once(self):
        product_request = _request()
        with patch('catalog.instagram_service.publish_story_to_instagram') as story_mock, \
                patch('catalog.instagram_service.publish_feed_to_instagram') as feed_mock:
            story_mock.return_value = {'container_id': 'c-story', 'media_id': 'm-story'}
            feed_mock.return_value = {'container_id': 'c-feed', 'media_id': 'm-feed'}
            process_instagram_publication_for_request(product_request.pk)
            process_instagram_publication_for_request(product_request.pk)
        publications = list(InstagramPublication.objects.filter(request=product_request))
        self.assertEqual(len(publications), 2)
        self.assertTrue(all(item.status == InstagramPublication.STATUS_QUEUED for item in publications))
        product_request.refresh_from_db()
        self.assertEqual(product_request.description, 'Передние колодки')

    def test_ambiguous_feed_stays_in_review_until_approval(self):
        product_request = _request(
            description='нужен правый передний',
            category='',
        )
        with patch('catalog.instagram_service.publish_story_to_instagram'), \
                patch('catalog.instagram_service.publish_feed_to_instagram') as feed_mock:
            process_instagram_publication_for_request(product_request.pk)
            feed = InstagramPublication.objects.get(
                request=product_request,
                placement=InstagramPublication.PLACEMENT_FEED,
            )
            self.assertEqual(feed.status, InstagramPublication.STATUS_NEEDS_REVIEW)
            self.assertEqual(feed.review_reason, 'ambiguous_part')
            self.assertIn('запчасть', feed.review_detail)

            queue_instagram_publication_for_processing(feed)
            feed.refresh_from_db()
            self.assertEqual(feed.status, InstagramPublication.STATUS_NEEDS_REVIEW)

            feed.status = InstagramPublication.STATUS_QUEUED
            feed.save()
            feed.refresh_from_db()
            self.assertEqual(feed.status, InstagramPublication.STATUS_NEEDS_REVIEW)
            self.assertFalse(feed.approved_at)
            feed_mock.assert_not_called()

            approve_instagram_publication(feed)
            feed.refresh_from_db()
            self.assertEqual(feed.status, InstagramPublication.STATUS_APPROVED)
            queue_instagram_publication_for_processing(feed)
            feed.refresh_from_db()
            self.assertEqual(feed.status, InstagramPublication.STATUS_QUEUED)

    def test_second_claim_does_not_take_the_same_row(self):
        product_request = _request(phone='77001112244')
        publication = InstagramPublication.objects.create(
            request=product_request,
            placement=InstagramPublication.PLACEMENT_STORY,
            status=InstagramPublication.STATUS_QUEUED,
            caption='story',
        )
        self.assertTrue(_claim_instagram_publication(publication))
        again = InstagramPublication.objects.get(pk=publication.pk)
        self.assertFalse(_claim_instagram_publication(again))
        again.refresh_from_db()
        self.assertEqual(again.status, InstagramPublication.STATUS_PUBLISHING)

    def test_network_error_fails_without_logging_the_token(self):
        product_request = _request(phone='77001112255')
        publication = InstagramPublication.objects.create(
            request=product_request,
            placement=InstagramPublication.PLACEMENT_STORY,
            status=InstagramPublication.STATUS_QUEUED,
            caption='story',
            image='instagram_stories/story.jpg',
        )
        with patch(
            'catalog.instagram_service.publish_story_to_instagram',
            side_effect=requests.ConnectionError('https://graph.facebook.com/?access_token=test-token'),
        ), patch('catalog.instagram_service._story_file_exists', return_value=True):
            publish_instagram_publication(publication)
        publication.refresh_from_db()
        self.assertEqual(publication.status, InstagramPublication.STATUS_FAILED)
        self.assertEqual(publication.retry_count, 0)
        self.assertNotIn('test-token', publication.error_message)
        self.assertNotIn('access_token', publication.error_message)

    def _allow_public(self, product_request, *, status=InstagramPublication.STATUS_QUEUED):
        return InstagramPublication.objects.create(
            request=product_request,
            placement=InstagramPublication.PLACEMENT_FEED,
            status=status,
            caption='public',
        )

    def test_public_page_hides_private_fields_and_uses_existing_seller_paths(self):
        product_request = _request(
            brand='Haval',
            model='Dargo',
            year=2023,
            description=(
                'фара правая и левая на хавал дарго желательно оригинал '
                'buyer@example.com'
            ),
            category='',
        )
        url = reverse('public_part_request', kwargs={'request_id': product_request.pk})
        self._allow_public(product_request)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('Haval', body)
        self.assertIn('Правая передняя фара', body)
        self.assertIn('Алматы', body)
        self.assertIn('noindex, nofollow', body)
        self.assertNotIn(product_request.phone, body)
        self.assertNotIn(product_request.vin, body)
        self.assertNotIn('buyer@example.com', body)
        self.assertNotIn(str(product_request.access_token), body)
        self.assertNotIn('wa.me', body)
        self.assertIn(reverse('request_parts_register'), body)
        self.assertIn(reverse('request_parts_form'), body)

        hidden = _request(status='deleted', phone='77009998877')
        self._allow_public(hidden, status=InstagramPublication.STATUS_PUBLISHED)
        missing = self.client.get(reverse('public_part_request', kwargs={'request_id': hidden.pk}))
        self.assertEqual(missing.status_code, 404)
        self.assertNotIn('77009998877', missing.content.decode())

        seller = Seller.objects.create(name='Магазин', whatsapp='77007654321')
        access = create_seller_request_access(request=product_request, seller=seller)
        before = SellerRequestAccess.objects.count()
        session = self.client.session
        session['seller_id'] = seller.id
        session.save()
        logged_in = self.client.get(url)
        self.assertEqual(logged_in.status_code, 200)
        logged_body = logged_in.content.decode()
        self.assertIn(reverse('request_parts_cabinet'), logged_body)
        self.assertNotIn(access.token, logged_body)
        self.assertNotIn(product_request.phone, logged_body)
        self.assertEqual(SellerRequestAccess.objects.count(), before)

    def test_public_url_does_not_enumerate_private_requests(self):
        private = _request(
            description='секретная фара buyer@hidden.example',
            phone='77005556677',
            vin='JHMCM56557C404453',
        )
        url = reverse('public_part_request', kwargs={'request_id': private.pk})
        before = SellerRequestAccess.objects.count()

        closed = self.client.get(url)
        self.assertEqual(closed.status_code, 404)
        closed_body = closed.content.decode()
        self.assertNotIn(private.phone, closed_body)
        self.assertNotIn(private.vin, closed_body)
        self.assertNotIn('buyer@hidden.example', closed_body)
        self.assertNotIn(str(private.access_token), closed_body)
        self.assertEqual(SellerRequestAccess.objects.count(), before)

        InstagramPublication.objects.create(
            request=private,
            placement=InstagramPublication.PLACEMENT_STORY,
            status=InstagramPublication.STATUS_DRAFT,
            caption='draft',
        )
        self.assertEqual(self.client.get(url).status_code, 404)

        InstagramPublication.objects.filter(request=private).update(
            status=InstagramPublication.STATUS_NEEDS_REVIEW,
        )
        self.assertEqual(self.client.get(url).status_code, 404)

        InstagramPublication.objects.filter(request=private).update(
            status=InstagramPublication.STATUS_FAILED,
        )
        self.assertEqual(self.client.get(url).status_code, 404)

        InstagramPublication.objects.filter(request=private).update(
            status=InstagramPublication.STATUS_PUBLISHED,
        )
        opened = self.client.get(url)
        self.assertEqual(opened.status_code, 200)
        opened_body = opened.content.decode()
        self.assertNotIn(private.phone, opened_body)
        self.assertNotIn(private.vin, opened_body)
        self.assertNotIn('buyer@hidden.example', opened_body)
        self.assertNotIn(str(private.access_token), opened_body)
        self.assertNotIn('wa.me', opened_body)
        self.assertEqual(SellerRequestAccess.objects.count(), before)
