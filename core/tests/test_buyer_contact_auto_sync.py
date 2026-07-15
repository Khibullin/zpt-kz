from __future__ import annotations

import json
from unittest.mock import patch

from django.test import Client, TestCase, override_settings

from core.models import (
    BuyerCategoryInterest,
    BuyerCityInterest,
    BuyerContact,
    BuyerVehicle,
    ContactConsent,
    Request,
)
from core.services.buyer_contact_service import (
    SYNC_STATUS_SYNCED,
    sync_buyer_contact_from_request,
)


CREATE_REQUEST_PAYLOAD = {
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
    'phone': '77011234567',
}


def post_create_request(client, payload=None, **extra):
    data = dict(CREATE_REQUEST_PAYLOAD)
    if payload:
        data.update(payload)
    data.update(extra)
    return client.post(
        '/api/create-request/',
        data=json.dumps(data),
        content_type='application/json',
    )


@patch('core.views._find_matching_sellers', return_value=([], 'none'))
@patch('core.views._build_dispatch_queue', return_value=[])
@patch('core.views._send_buyer_whatsapp_notification_async')
@patch('core.views.schedule_instagram_publication_for_request')
class CreateRequestBuyerAutoSyncTests(TestCase):
    def setUp(self):
        self.client = Client()

    @override_settings(INSTAGRAM_PUBLISH_MODE='TEST')
    def test_successful_request_creates_buyer_contact(
        self,
        instagram_mock,
        buyer_whatsapp_mock,
        dispatch_queue_mock,
        matching_mock,
    ):
        with self.captureOnCommitCallbacks(execute=True):
            response = post_create_request(self.client)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['status'], 'ok')

        req = Request.objects.get(pk=body['id'])
        self.assertIsNotNone(req.buyer_contact_id)

        buyer = req.buyer_contact
        self.assertEqual(buyer.phone_normalized, '77011234567')
        self.assertEqual(buyer.requests_count, 1)
        self.assertEqual(buyer.vehicles.count(), 1)
        self.assertEqual(buyer.category_interests.count(), 1)
        self.assertEqual(buyer.city_interests.count(), 1)
        self.assertEqual(buyer.consents.count(), 3)

    @override_settings(INSTAGRAM_PUBLISH_MODE='TEST')
    def test_two_requests_same_phone_share_one_buyer(
        self,
        instagram_mock,
        buyer_whatsapp_mock,
        dispatch_queue_mock,
        matching_mock,
    ):
        with self.captureOnCommitCallbacks(execute=True):
            first = post_create_request(self.client, phone='77012223344')
            second = post_create_request(
                self.client,
                phone='77012223344',
                brand='Honda',
                model='Civic',
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)

        self.assertEqual(BuyerContact.objects.filter(phone_normalized='77012223344').count(), 1)
        buyer = BuyerContact.objects.get(phone_normalized='77012223344')
        self.assertEqual(buyer.requests_count, 2)
        self.assertEqual(buyer.vehicles.count(), 2)
        self.assertEqual(buyer.category_interests.count(), 1)
        self.assertEqual(buyer.category_interests.get().requests_count, 2)
        self.assertEqual(buyer.city_interests.get().requests_count, 2)

    @override_settings(INSTAGRAM_PUBLISH_MODE='TEST')
    def test_eight_and_seven_prefix_share_one_buyer_via_endpoint(
        self,
        instagram_mock,
        buyer_whatsapp_mock,
        dispatch_queue_mock,
        matching_mock,
    ):
        with self.captureOnCommitCallbacks(execute=True):
            first = post_create_request(self.client, phone='77013334455')
            second = post_create_request(self.client, phone='87013334455')

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)

        self.assertEqual(BuyerContact.objects.filter(phone_normalized='77013334455').count(), 1)
        req_ids = [first.json()['id'], second.json()['id']]
        linked_buyer_ids = set(
            Request.objects.filter(pk__in=req_ids).values_list('buyer_contact_id', flat=True),
        )
        self.assertEqual(len(linked_buyer_ids), 1)

    @override_settings(INSTAGRAM_PUBLISH_MODE='TEST')
    def test_invalid_phone_skips_buyer_sync_but_request_succeeds(
        self,
        instagram_mock,
        buyer_whatsapp_mock,
        dispatch_queue_mock,
        matching_mock,
    ):
        with self.captureOnCommitCallbacks(execute=True):
            response = post_create_request(self.client, phone='invalid')

        self.assertEqual(response.status_code, 200)
        req = Request.objects.get(pk=response.json()['id'])
        self.assertIsNone(req.buyer_contact_id)
        self.assertEqual(BuyerContact.objects.count(), 0)

    @override_settings(INSTAGRAM_PUBLISH_MODE='TEST')
    @patch('core.views.sync_buyer_contact_from_request', side_effect=RuntimeError('db down'))
    def test_sync_exception_does_not_break_request_creation(
        self,
        sync_mock,
        instagram_mock,
        buyer_whatsapp_mock,
        dispatch_queue_mock,
        matching_mock,
    ):
        with self.assertLogs('core.views', level='ERROR') as logs:
            with self.captureOnCommitCallbacks(execute=True):
                response = post_create_request(self.client, phone='77014445566')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Request.objects.filter(phone='77014445566').exists())
        self.assertTrue(
            any('Buyer contact sync failed for request #' in message for message in logs.output),
        )
        matching_mock.assert_called_once()
        buyer_whatsapp_mock.assert_called_once()
        instagram_mock.assert_called_once()

    @override_settings(INSTAGRAM_PUBLISH_MODE='TEST')
    def test_on_commit_runs_buyer_sync_before_instagram(
        self,
        instagram_mock,
        buyer_whatsapp_mock,
        dispatch_queue_mock,
        matching_mock,
    ):
        call_order: list[str] = []

        def track_sync(request_obj):
            call_order.append('buyer_sync')
            return sync_buyer_contact_from_request(request_obj)

        def track_instagram(request_id):
            call_order.append('instagram')

        with patch(
            'core.views.sync_buyer_contact_from_request',
            side_effect=track_sync,
        ):
            instagram_mock.side_effect = track_instagram
            with self.captureOnCommitCallbacks(execute=True):
                response = post_create_request(self.client, phone='77015556677')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(call_order, ['buyer_sync', 'instagram'])
        self.assertTrue(BuyerContact.objects.filter(phone_normalized='77015556677').exists())


class BuyerAutoSyncServiceLevelTests(TestCase):
    def test_eight_prefix_normalized_at_service_level(self):
        req = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Camry',
            category='Тормоза',
            city='Алматы',
            phone='87016667788',
        )
        result = sync_buyer_contact_from_request(req)

        self.assertEqual(result.status, SYNC_STATUS_SYNCED)
        req.refresh_from_db()
        self.assertEqual(req.buyer_contact.phone_normalized, '77016667788')
