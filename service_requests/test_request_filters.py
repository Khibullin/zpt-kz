import json
from unittest.mock import patch
from urllib.parse import urlsplit
from django.contrib.auth import get_user_model
from django.core import signing
from django.test import Client, TestCase
from core.models import Country, Brand, CarModel
from core.whatsapp_redaction import redact_whatsapp_sensitive_data
from service_requests.models import Service, ServiceSeller, ServiceRequest, ServiceMatch, ServiceBroadcastSettings, ServiceRequestDispatch
from service_requests.services.request_filters import save_filters, matches_request, initial_filters
from service_requests.services.dispatch_policy import select_service_sellers_for_request
from service_requests.services.whatsapp_dispatch import enqueue_service_request_dispatches, process_due_service_request_dispatches, service_request_template_call, _deliver_claimed_dispatch
from service_requests.filter_views import personal_request_url, SALT, MAX_AGE


class ServiceFilterTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Япония')
        self.brand = Brand.objects.create(name='Toyota', transport_type='car', country=country)
        self.model = CarModel.objects.create(name='Camry', brand=self.brand, transport_type='car')
        self.other = Brand.objects.create(name='Lexus', transport_type='car', country=country)
        self.other_model = CarModel.objects.create(name='RX', brand=self.other, transport_type='car')
        self.user = get_user_model().objects.create_user(username='filter-owner', password='some-test-password')
        self.seller = ServiceSeller.objects.create(name='СТО', whatsapp='77011112233', seller_type='sto', city='Алматы', district='Бостандыкский', user=self.user)
        self.service = Service.objects.create(name='Диагностика')
        self.seller.services.add(self.service)
        self.req = ServiceRequest.objects.create(service_type='sto', city='Алматы', district='Бостандыкский', brand='Toyota', model='Camry', phone='77012223344')
        self.req.services.add(self.service)
        ServiceBroadcastSettings.objects.create(mode='live')
        self.match = ServiceMatch.objects.create(request=self.req, seller=self.seller)
        self.path = urlsplit(personal_request_url(self.req, self.seller)).path
        self.data = dict(transport_types=['car'], all_brands=False, brands=[dict(id=self.brand.pk, all_models=False, model_ids=[self.model.pk])], all_categories=False, category_ids=[1])

    def save(self, **changes):
        self.seller = save_filters(self.seller, {**self.data, **changes})

    def test_save_shared_profile_and_no_control_changes(self):
        self.save()
        self.assertEqual(initial_filters(self.seller), self.data)
        self.assertTrue(self.seller.receive_requests)
        self.assertEqual(self.seller.city, 'Алматы')
        self.assertEqual(self.seller.seller_type, 'sto')
        self.assertEqual(list(self.seller.services.values_list('name', flat=True)), ['Диагностика'])
        self.client.force_login(self.user)
        response = self.client.get('/api/service/service-seller-profile/')
        self.assertIn('Toyota: Camry', str(response.json()['request_filter_rows']))
        response = self.client.get(f'/catalog/services/{self.seller.pk}/')
        self.assertContains(response, 'Toyota: Camry')
        self.assertContains(self.client.get('/service-request/filters/'), 'Виды ремонтных работ')

    def test_strict_vehicle_and_service_matching(self):
        self.save()
        self.assertEqual(select_service_sellers_for_request(self.req), [self.seller])
        for field, value in [('brand', 'Lexus'), ('brand', ''), ('model', 'RAV4'), ('model', ''), ('transport_type', 'truck'), ('service_type', 'detailing')]:
            old = getattr(self.req, field)
            setattr(self.req, field, value)
            self.assertFalse(matches_request(self.seller, self.req))
            self.assertEqual(select_service_sellers_for_request(self.req), [])
            setattr(self.req, field, old)
        self.req.services.set([Service.objects.create(name='Кузовной ремонт')])
        self.assertFalse(matches_request(self.seller, self.req))

    def test_all_choices_keep_geography_and_direction(self):
        self.save(transport_types=['car', 'truck'], all_brands=True, all_categories=True)
        self.req.transport_type = 'truck'; self.req.brand = ''; self.req.model = ''
        self.assertTrue(matches_request(self.seller, self.req))
        self.req.city = 'Астана'
        self.assertEqual(select_service_sellers_for_request(self.req), [])
        self.req.city = 'Алматы'; self.req.service_type = 'detailing'
        self.assertEqual(select_service_sellers_for_request(self.req), [])

    def test_unconfigured_seller_retains_legacy_matching(self):
        self.req.brand = 'Lexus'; self.req.transport_type = 'truck'
        self.assertEqual(select_service_sellers_for_request(self.req), [self.seller])
        self.seller.refresh_from_db(); self.assertIsNone(self.seller.request_filters)

    def test_invalid_data_rolls_back(self):
        for changes in [dict(transport_types=[]), dict(category_ids=[999]), dict(all_categories='true'), dict(brands=[dict(id=self.brand.pk, all_models=False, model_ids=[self.other_model.pk])])]:
            with self.assertRaises(ValueError):
                self.save(**changes)
            self.seller.refresh_from_db(); self.assertIsNone(self.seller.request_filters)

    def test_queue_and_sender_recheck(self):
        enqueue_service_request_dispatches(self.req, [self.seller])
        self.save(brands=[dict(id=self.other.pk, all_models=True, model_ids=[])])
        self.assertEqual(enqueue_service_request_dispatches(self.req, [self.seller]), [])
        with patch('service_requests.services.whatsapp_dispatch.send_whatsapp_template_message') as send:
            result = process_due_service_request_dispatches()
            self.assertEqual(result['skipped'], 1)
            send.assert_not_called()
        dispatch = ServiceRequestDispatch.objects.get(request=self.req, seller=self.seller)
        self.assertEqual(dispatch.skip_reason, 'specialization_mismatch')
        dispatch.status = 'processing'; dispatch.save()
        with patch('service_requests.services.whatsapp_dispatch.send_whatsapp_template_message') as send:
            _deliver_claimed_dispatch(dispatch.pk); send.assert_not_called()

    @patch.dict('os.environ', {'GOOGLE_TAG_MANAGER_ID': 'GTM-TEST123'})
    def test_csrf_expiration_and_bound_identity(self):
        client = Client(enforce_csrf_checks=True)
        url = self.path + 'filters/'
        self.assertEqual(client.get(url).status_code, 200)
        self.assertEqual(client.post(url, {'filters': json.dumps(self.data)}).status_code, 403)
        csrf = client.cookies['csrftoken'].value
        response = client.post(url, {'filters': json.dumps(self.data), 'csrfmiddlewaretoken': csrf, 'seller_id': 999999})
        self.assertEqual(response.status_code, 302)
        self.seller.refresh_from_db(); self.assertIsNotNone(self.seller.request_filters)
        response = client.get(self.path)
        self.assertContains(response, 'Написать клиенту в WhatsApp')
        self.assertNotContains(response, 'googletagmanager.com')
        self.assertNotContains(client.get(url), 'googletagmanager.com')
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertIn('no-store', response['Cache-Control'])
        with patch('django.core.signing.time.time', return_value=1):
            token = signing.dumps({'match': self.match.pk}, salt=SALT)
        expired = '/service-request/personal/' + token + '/'
        self.assertEqual(client.get(expired).status_code, 410)
        self.assertEqual(client.post(expired + 'filters/', {'csrfmiddlewaretoken': csrf, 'filters': json.dumps(self.data)}).status_code, 410)
        self.assertEqual(client.get('/service-request/personal/invalid/filters/').status_code, 404)
        self.seller.is_test_seller = True; self.seller.save()
        self.assertContains(client.get(self.path), 'Настроить фильтр')
        self.assertEqual(client.get(url).status_code, 200)
        self.assertEqual(client.post(url, {'csrfmiddlewaretoken': csrf, 'filters': json.dumps(self.data)}).status_code, 302)
        self.seller.refresh_from_db()
        self.assertIsNotNone(self.seller.request_filters)
        self.seller.is_active = False; self.seller.save()
        self.assertEqual(client.get(url).status_code, 403)

    def test_cabinet_requires_owner_and_contact_save_preserves_filters(self):
        self.assertEqual(self.client.get('/service-request/filters/').status_code, 302)
        stranger = get_user_model().objects.create_user(username='stranger')
        self.client.force_login(stranger)
        self.assertEqual(self.client.get('/service-request/filters/?seller_id=' + str(self.seller.pk)).status_code, 403)
        self.client.force_login(self.user)
        self.save(); before = self.seller.request_filters
        r = self.client.post('/api/service/update-service-seller-profile/', json.dumps(dict(name='Новое имя', seller_type='detailing', services=['Мойка'])), content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.seller.refresh_from_db()
        self.assertEqual(self.seller.request_filters, before)
        self.assertEqual(self.seller.seller_type, 'sto')
        self.assertEqual(list(self.seller.services.values_list('name', flat=True)), ['Диагностика'])

    def test_detailing_form_and_service_names(self):
        self.seller.seller_type = 'detailing'; self.seller.save()
        self.save(category_ids=[2, 3])
        self.req.service_type = 'detailing'
        self.req.services.set([Service.objects.get(name='Химчистка салона')])
        self.assertTrue(matches_request(self.seller, self.req))
        self.client.force_login(self.user)
        page = self.client.get('/service-request/filters/')
        self.assertContains(page, 'Услуги детейлинга и тюнинга')
        self.assertNotContains(page, 'Категории запчастей')
        self.assertNotContains(page, 'Кузовной ремонт')

    def test_whatsapp_personal_link_is_complete_and_redacted_in_logs(self):
        self.req.description = 'x' * 1000
        args = service_request_template_call(self.req, self.seller)
        text = args['body_parameters'][5]['text']
        url = text.split(' ')[-1]
        self.assertTrue(url.endswith('/'))
        self.assertEqual(self.client.get(urlsplit(url).path).status_code, 200)
        self.assertNotIn(urlsplit(url).path.split('/')[-2], redact_whatsapp_sensitive_data(text))

    def test_buyer_transport_saved_and_validated(self):
        data = dict(service_type='sto', transport_type='truck', city='Алматы', district='Бостандыкский', phone='77012223344', services=['Диагностика'])
        response = self.client.post('/api/service/create-service-request/', json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ServiceRequest.objects.latest('pk').transport_type, 'truck')
        data['transport_type'] = 'invalid'
        self.assertEqual(self.client.post('/api/service/create-service-request/', json.dumps(data), content_type='application/json').status_code, 400)
