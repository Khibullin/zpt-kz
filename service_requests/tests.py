from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.contrib.staticfiles.finders import find
from django.test import Client, TestCase
from django.urls import reverse

from service_requests.models import Service, ServiceRequest, ServiceSeller
from service_requests.views import match_services


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
        self.assertIn('sellers_count', data)

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
        self.assertContains(response, 'service-request-form-v2.js')

    def test_service_request_js_builds_dom_without_html_string_success(self):
        js_path = find('js/service-request-form-v2.js')
        self.assertIsNotNone(js_path)
        content = Path(js_path).read_text(encoding='utf-8')

        self.assertIn('renderSuccessResult', content)
        self.assertIn('createElement', content)
        self.assertIn('textContent', content)
        self.assertNotIn("setMessage(`\n<b>", content)
        self.assertNotIn('<b>✅ Заявка принята', content)


class ServiceRequestSuccessMessageTests(TestCase):
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
        }
        payload.update(overrides)
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type='application/json',
        )

    @patch('service_requests.views.send_service_whatsapp_to_seller')
    def test_city_with_sellers_uses_sent_to_executors_message(self, mock_send):
        service = Service.objects.create(name='Диагностика')
        seller = ServiceSeller.objects.create(
            name='Almaty seller',
            whatsapp='77001111111',
            password=make_password('secret'),
            city='Алматы',
            district='Бостандыкский',
            seller_type='sto',
            is_active=True,
        )
        seller.services.add(service)

        response = self._post_request()
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data['sellers_count'], 1)
        self.assertIn('отправлена подходящим исполнителям', data['title'])
        self.assertIn('5–15 минут', data['timing_hint'])
        self.assertEqual(data['result_button_label'], 'Посмотреть исполнителей по заявке')

    @patch('service_requests.views.send_service_whatsapp_to_seller')
    def test_city_without_sellers_uses_saved_request_message(self, mock_send):
        response = self._post_request(city='Кызылорда', district='')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        req = ServiceRequest.objects.get(pk=data['request_id'])

        self.assertEqual(data['sellers_count'], 0)
        self.assertNotIn('отправлена исполнителям', data['title'])
        self.assertNotIn('отправлена подходящим', data['title'])
        self.assertEqual(data['title'], '✅ Заявка принята.')
        self.assertIn('нет зарегистрированных исполнителей', data['message'])
        self.assertEqual(data['timing_hint'], '')
        self.assertNotIn('5–15 минут', json.dumps(data, ensure_ascii=False))
        self.assertEqual(
            data['result_url'],
            reverse('service_request_result_page', args=[req.id]),
        )
        self.assertEqual(data['result_button_label'], 'Посмотреть страницу заявки')

    @patch('service_requests.views.send_service_whatsapp_to_seller')
    def test_result_page_without_sellers_does_not_claim_sent(self, mock_send):
        response = self._post_request(city='Кызылорда', district='')
        req_id = response.json()['request_id']

        page = self.client.get(reverse('service_request_result_page', args=[req_id]))
        self.assertEqual(page.status_code, 200)
        content = page.content.decode('utf-8')
        self.assertIn('✅ Заявка №{} принята.'.format(req_id), content)
        self.assertNotIn('отправлена исполнителям', content)
        self.assertNotIn('5–15 минут', content)


class ServiceRequestCityDistrictTests(TestCase):
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
        }
        payload.update(overrides)
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_almaty_valid_district_saved(self):
        response = self._post_request()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        req = ServiceRequest.objects.get(pk=data['request_id'])
        self.assertEqual(req.city, 'Алматы')
        self.assertEqual(req.district, 'Бостандыкский')

    def test_almaty_invalid_district_returns_400(self):
        response = self._post_request(district='Несуществующий')
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())
        self.assertFalse(ServiceRequest.objects.filter(city='Алматы', district='Несуществующий').exists())

    def test_kokshetau_with_almaty_district_clears_district(self):
        response = self._post_request(city='Кокшетау', district='Ауэзовский')
        self.assertEqual(response.status_code, 200)
        req = ServiceRequest.objects.get(pk=response.json()['request_id'])
        self.assertEqual(req.city, 'Кокшетау')
        self.assertEqual(req.district, '')

    def test_kokshetau_without_district_saved_empty(self):
        response = self._post_request(city='Кокшетау', district='')
        self.assertEqual(response.status_code, 200)
        req = ServiceRequest.objects.get(pk=response.json()['request_id'])
        self.assertEqual(req.city, 'Кокшетау')
        self.assertEqual(req.district, '')

    def test_almaty_without_district_returns_400(self):
        response = self._post_request(district='')
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())

    def test_template_has_district_field_elements(self):
        response = self.client.get('/service-request/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('id="districtField"', content)
        self.assertIn('id="district"', content)
        self.assertNotIn('id="districtField" class="field hidden"', content)
        self.assertNotIn('class="field hidden" id="districtField"', content)

    def test_template_district_select_has_no_hardcoded_almaty_options(self):
        response = self.client.get('/service-request/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('Сначала выберите город', content)
        self.assertNotIn('<option value="Ауэзовский">', content)
        self.assertNotIn('<option value="Бостандыкский">', content)

    def test_form_js_updates_districts_by_city(self):
        js_path = find('js/service-request-form-v2.js')
        self.assertIsNotNone(js_path)
        content = Path(js_path).read_text(encoding='utf-8')
        self.assertIn('updateDistrictField', content)
        self.assertIn('initServiceRequestForm', content)
        self.assertIn("cityEl.addEventListener('change',updateDistrictField)", content)
        self.assertIn('updateDistrictField();', content)
        self.assertIn("'Алматы'", content)
        self.assertIn("'Астана'", content)

    def test_template_uses_service_result_v5_cache_bust(self):
        response = self.client.get('/service-request/')
        self.assertContains(response, 'service-request-form-v2.js?v=service_result_v5')
        self.assertContains(response, 'portal-forms.css?v=service_result_v4')

    @patch('service_requests.views.send_service_whatsapp_to_seller')
    def test_match_services_non_almaty_does_not_filter_by_almaty_district(self, mock_send):
        service = Service.objects.create(name='Диагностика')
        almaty_seller = ServiceSeller.objects.create(
            name='Almaty seller',
            whatsapp='77001111111',
            password=make_password('secret'),
            city='Алматы',
            district='Бостандыкский',
            seller_type='sto',
            is_active=True,
        )
        almaty_seller.services.add(service)

        kokshetau_seller = ServiceSeller.objects.create(
            name='Kokshetau seller',
            whatsapp='77002222222',
            password=make_password('secret'),
            city='Кокшетау',
            district='',
            seller_type='sto',
            is_active=True,
        )
        kokshetau_seller.services.add(service)

        req = ServiceRequest.objects.create(
            service_type='sto',
            city='Кокшетау',
            district='',
            phone='77003333333',
        )
        req.services.add(service)

        matched = match_services(req)

        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].pk, kokshetau_seller.pk)
        mock_send.assert_called_once()
