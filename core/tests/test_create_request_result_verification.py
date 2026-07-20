"""Verification: create-request JSON for /request-parts/ result screen (no real WhatsApp)."""
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from core.buyer_portal import (
    BUYER_STATUS_DIRECT,
    BUYER_STATUS_PENDING,
    BUYER_STATUS_SENT,
)
from core.models import BroadcastSettings, Match, Request, RequestDispatch, Seller
from core.tests.test_request_dispatch_waves import _create_sellers, _ensure_broadcast_settings

FORBIDDEN_LABELS = ('Ошибка отправки', 'Ошибка отправки WhatsApp')
VISIBLE_LIMIT = 8


def _post_create_request(sellers, *, send_side_effect=None):
    client = Client()
    send_return = {'ok': True, 'message_id': 'wamid.test'}
    with patch('core.views._send_buyer_whatsapp_notification_async'), patch(
        'core.views._find_matching_sellers',
        return_value=(sellers, 'matched'),
    ), patch(
        'core.views.send_whatsapp_template',
        side_effect=send_side_effect,
        return_value=None if send_side_effect else send_return,
    ):
        response = client.post(
            '/api/create-request/',
            data={
                'transport_type': 'car',
                'brand': 'Toyota',
                'model': 'Camry',
                'category': 'Тормоза',
                'city': 'Алматы',
                'phone': '77001112233',
                'search_scope': 'city',
            },
        )
    return response


def _assert_common_payload(payload):
    assert 'request_page_url' in payload
    assert payload['request_page_url'].startswith('https://zpt.kz/my-request/')
    assert 'sellers_hidden_count' in payload
    assert 'seller_notifications' in payload
    raw = str(payload)
    for label in FORBIDDEN_LABELS:
        assert label not in raw
    for item in payload['seller_notifications']:
        assert 'status_label' in item
        for label in FORBIDDEN_LABELS:
            assert label not in item['status_label']


@override_settings(PUBLIC_BASE_URL='https://zpt.kz')
class CreateRequestResultVerificationTests(TestCase):
    def setUp(self):
        _ensure_broadcast_settings(
            mode=BroadcastSettings.MODE_LIVE,
            wave_size=10,
            wave_interval_minutes=5,
            emergency_stop=False,
        )

    def test_sent_status_in_response(self):
        sellers = _create_sellers(3)
        response = _post_create_request(sellers)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        _assert_common_payload(payload)
        labels = {item['status_label'] for item in payload['seller_notifications']}
        self.assertEqual(labels, {BUYER_STATUS_SENT})
        self.assertEqual(payload['sellers_hidden_count'], 0)

    def test_pending_status_for_later_wave_sellers(self):
        _ensure_broadcast_settings(wave_size=3)
        sellers = _create_sellers(5)
        response = _post_create_request(sellers)
        payload = response.json()
        _assert_common_payload(payload)
        statuses = {item['whatsapp_status'] for item in payload['seller_notifications']}
        self.assertIn('sent', statuses)
        self.assertIn('pending', statuses)
        pending_labels = [
            item['status_label']
            for item in payload['seller_notifications']
            if item['whatsapp_status'] == 'pending'
        ]
        self.assertTrue(all(label == BUYER_STATUS_PENDING for label in pending_labels))

    def test_failed_error_maps_to_direct_contact_label(self):
        sellers = _create_sellers(1)

        def _always_fail(*args, **kwargs):
            return {'ok': False, 'error': 'HTTP 400'}

        response = _post_create_request(sellers, send_side_effect=_always_fail)
        payload = response.json()
        _assert_common_payload(payload)
        item = payload['seller_notifications'][0]
        self.assertEqual(item['whatsapp_status'], 'error')
        self.assertEqual(item['status_label'], BUYER_STATUS_DIRECT)

    def test_more_than_eight_sellers_hidden_count_and_visible_order(self):
        sellers = _create_sellers(10)
        response = _post_create_request(sellers)
        payload = response.json()
        _assert_common_payload(payload)
        self.assertEqual(len(payload['seller_notifications']), 10)
        self.assertEqual(payload['sellers_hidden_count'], 2)
        visible = payload['seller_notifications'][:VISIBLE_LIMIT]
        hidden = payload['seller_notifications'][VISIBLE_LIMIT:]
        self.assertEqual(len(visible), 8)
        self.assertEqual(len(hidden), 2)

    def test_success_log_overrides_failed_dispatch_in_response(self):
        req = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Camry',
            category='Тормоза',
            city='Алматы',
            phone='77001112233',
            status='sent',
        )
        seller = Seller.objects.create(
            name='Log override seller',
            whatsapp='77005559999',
            transport_type='car',
            city='Алматы',
        )
        RequestDispatch.objects.create(
            request=req,
            seller=seller,
            wave_number=1,
            position_number=1,
            status=RequestDispatch.STATUS_FAILED,
            scheduled_at=timezone.now(),
        )
        Match.objects.create(request=req, seller=seller, status='error')
        from core.models import WhatsAppMessageLog

        WhatsAppMessageLog.objects.create(
            request_id=req.id,
            seller_name=seller.name,
            phone_clean='77005559999',
            is_success=True,
            status_text='sent',
            message_id='wamid.verify',
        )
        from core.buyer_portal import build_seller_notifications_payload
        from core.views import _buyer_contact_link
        from django.urls import reverse

        payload = build_seller_notifications_payload(
            req,
            get_buyer_wa_link=_buyer_contact_link,
            get_profile_url=lambda seller_id: reverse(
                'parts_seller_detail_public',
                kwargs={'seller_id': seller_id},
            ),
        )
        item = payload['seller_notifications'][0]
        self.assertEqual(item['status_label'], BUYER_STATUS_SENT)
        self.assertNotIn('Ошибка отправки', item['status_label'])
