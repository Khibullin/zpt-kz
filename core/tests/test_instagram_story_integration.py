import json
from unittest.mock import patch

from django.test import Client, TestCase

from catalog.image_generator import InstagramStoryGenerationError


class CreateRequestInstagramStoryTests(TestCase):
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

    @patch('core.views._dispatch_due_requests')
    @patch('core.views._find_matching_sellers', return_value=([], 'none'))
    @patch('core.views._build_dispatch_queue', return_value=[])
    @patch('core.views._send_buyer_whatsapp_notification_async')
    @patch('core.views.try_generate_instagram_story')
    def test_create_request_calls_story_generator(
        self,
        story_mock,
        buyer_whatsapp_mock,
        dispatch_queue_mock,
        matching_mock,
        due_mock,
    ):
        response = self.client.post(
            '/api/create-request/',
            data=json.dumps(self.payload),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        story_mock.assert_called_once()

    @patch('core.views._dispatch_due_requests')
    @patch('core.views._find_matching_sellers', return_value=([], 'none'))
    @patch('core.views._build_dispatch_queue', return_value=[])
    @patch('core.views._send_buyer_whatsapp_notification_async')
    @patch(
        'catalog.image_generator.generate_instagram_story',
        side_effect=InstagramStoryGenerationError('render failed'),
    )
    def test_create_request_succeeds_when_story_generation_fails(
        self,
        generate_mock,
        buyer_whatsapp_mock,
        dispatch_queue_mock,
        matching_mock,
        due_mock,
    ):
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
