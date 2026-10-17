"""Both legacy input formats must validate content before any side effects."""
import json
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from core.views import create_request


class ClassicPartsPreflightTests(SimpleTestCase):
    def test_rejections_cannot_be_confirmed_or_hidden_by_an_article(self):
        for content_type in ('multipart', 'json'):
            for text, code in (
                ('Стекло лобовое', 'category_mismatch'),
                ('ищу работу', 'not_parts_request'),
                ('абракадабра', 'unspecified_part'),
            ):
                with self.subTest(content_type=content_type, text=text):
                    data = {
                        'description': text, 'category': 'Топливная система',
                        'article': '52119-0K040', 'warning_confirmed': '1',
                    }
                    factory = RequestFactory()
                    if content_type == 'json':
                        request = factory.post('/api/create-request/', data=json.dumps(data),
                                               content_type='application/json')
                    else:
                        request = factory.post('/api/create-request/', data=data)
                    with patch('core.views.Request.objects.create') as create, patch(
                        'core.views._send_buyer_whatsapp_notification_async'
                    ) as notify, patch('core.views._find_matching_sellers') as matching:
                        response = create_request(request)
                    self.assertEqual(response.status_code, 422)
                    body = json.loads(response.content)
                    self.assertEqual(body['rejection_code'], code)
                    self.assertTrue(body['error'])
                    create.assert_not_called()
                    notify.assert_not_called()
                    matching.assert_not_called()

    def test_ambiguous_query_requires_confirmation(self):
        request = RequestFactory().post('/api/create-request/', data={
            'description': 'руль', 'category': 'Топливная система',
        })
        with patch('core.views.Request.objects.create') as create:
            response = create_request(request)
        self.assertEqual(response.status_code, 422)
        self.assertTrue(json.loads(response.content)['warning_required'])
        create.assert_not_called()
