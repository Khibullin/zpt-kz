from __future__ import annotations

import json
from pathlib import Path

from django.contrib.staticfiles.finders import find
from django.test import Client, TestCase
from django.urls import reverse

from service_requests.models import ServiceRequest


class CreateServiceRequestResponseTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = '/api/service/create-service-request/'

    def _post_request(self, **overrides):
        payload = {
            'service_type': 'sto',
            'city': 'Алматы',
            'district': 'Бостандыкский',
            'phone': '77001234567',
            'services': ['Диагностика'],
            'description': 'Нужна диагностика ходовой',
        }
        payload.update(overrides)
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_success_response_is_structured_without_html(self):
        response = self._post_request()
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertTrue(data['success'])
        self.assertIn('request_id', data)
        self.assertIn('title', data)
        self.assertIn('message', data)
        self.assertIn('result_url', data)

        serialized = json.dumps(data, ensure_ascii=False)
        self.assertNotIn('<b>', serialized)
        self.assertNotIn('<br>', serialized)
        self.assertNotIn('<a ', serialized)
        self.assertNotIn('<a>', serialized)

    def test_success_response_includes_request_fields(self):
        response = self._post_request(
            description='Стук спереди',
            services=['Диагностика', 'Ходовая часть'],
        )
        data = response.json()
        req = ServiceRequest.objects.get(pk=data['request_id'])

        self.assertEqual(data['service_type'], 'sto')
        self.assertEqual(data['city'], 'Алматы')
        self.assertEqual(data['district'], 'Бостандыкский')
        self.assertEqual(data['phone'], '77001234567')
        self.assertEqual(data['description'], 'Стук спереди')
        self.assertEqual(data['services'], ['Диагностика', 'Ходовая часть'])
        self.assertEqual(
            data['result_url'],
            reverse('service_request_result_page', args=[req.id]),
        )

    def test_description_with_html_returned_as_plain_text(self):
        malicious = '<script>alert(1)</script><b>bold</b>'
        response = self._post_request(description=malicious)
        data = response.json()

        self.assertEqual(data['description'], malicious)
        self.assertNotIn('<b>', data['title'])
        self.assertNotIn('<b>', data['message'])


class ServiceRequestFormFrontendTests(TestCase):
    def test_form_page_loads_service_request_script(self):
        response = self.client.get('/service-request/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'service-request-form.js')

    def test_service_request_js_builds_dom_without_html_string_success(self):
        js_path = find('js/service-request-form.js')
        self.assertIsNotNone(js_path)
        content = Path(js_path).read_text(encoding='utf-8')

        self.assertIn('renderSuccessResult', content)
        self.assertIn('createElement', content)
        self.assertIn('textContent', content)
        self.assertNotIn("setMessage(`\n<b>", content)
        self.assertNotIn('<b>✅ Заявка принята', content)
