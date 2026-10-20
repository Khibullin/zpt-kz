import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from core.models import BroadcastSettings, Request, RequestDispatch, PartCategory
from core.tests.test_home_parts_request import _core_vehicle
from core.whatsapp_template_sender import send_whatsapp_template_message
from service_requests.models import ServiceBroadcastSettings, ServiceRequest, ServiceRequestDispatch
from service_requests.services.whatsapp_dispatch import process_due_service_request_dispatches


@override_settings(MANUAL_REQUEST_PROCESSING=True, REQUEST_OWNER_EMAIL_ENABLED=True,
                   REQUEST_OWNER_EMAIL='owner@test.local', EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ManualRequestProcessingTests(TestCase):
    def setUp(self):
        self.brand, self.model = _core_vehicle()
        PartCategory.objects.get_or_create(name="Тормоза")
        control = BroadcastSettings.load()
        control.mode = 'live'
        control.emergency_stop = False
        control.save()
        ServiceBroadcastSettings.objects.update_or_create(pk=1, defaults={'mode': 'live'})

    @patch('core.whatsapp_template_sender.validate_whatsapp_sender_config')
    @patch('core.whatsapp_template_sender.urllib.request.urlopen')
    def test_all_templates_blocked_before_config_and_network(self, http, config):
        for name in ('buyer_receipt', 'seller_request', 'service_request', 'marketing'):
            result = send_whatsapp_template_message('77012345678', template_name=name)
            self.assertFalse(result['ok'])
            self.assertEqual(result['error_code'], 'manual_request_processing')
        config.assert_not_called()
        http.assert_not_called()

    @patch('core.views.schedule_instagram_publication_for_request')
    @patch('core.services.operations_email_notifications.send_mail', return_value=1)
    @patch('core.whatsapp_template_sender.urllib.request.urlopen')
    def test_new_and_used_forms_save_email_once_without_queue(self, http, mail, instagram):
        for used in (False, True):
            data = dict(query='тормозные колодки', brand='Toyota', model='Camry',
                        brand_id=self.brand.pk, model_id=self.model.pk, city='Алматы',
                        phone='77015556677', transport_type='car', category='Тормоза',
                        idempotency_key=f'manual-{used}', part_condition='used' if used else 'new', is_dismantler_request='1' if used else '0')
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post('/api/home-parts-request/', data)
            self.assertEqual(response.status_code, 200, response.content)
            req = Request.objects.get(pk=response.json()['id'])
            self.assertEqual(req.is_dismantler_request, used)
            self.assertFalse(response.json()['queued'])
            self.assertIn('Ваша заявка принята', response.json()['message'])
            with self.captureOnCommitCallbacks(execute=True):
                replay = self.client.post('/api/home-parts-request/', data)
            self.assertTrue(replay.json()['replay'])
        self.assertEqual(mail.call_count, 2)
        self.assertFalse(RequestDispatch.objects.exists())
        http.assert_not_called()

    @patch('core.services.operations_email_notifications.send_mail', return_value=1)
    @patch('core.whatsapp_template_sender.urllib.request.urlopen')
    def test_sto_and_detailing_save_email_and_render_without_queue(self, http, mail):
        for direction in ('sto', 'detailing'):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post('/api/service/create-service-request/',
                    json.dumps(dict(service_type=direction, city='Алматы', district='Бостандыкский', phone='77015556677',
                                    services=['Диагностика'], description='Проверить автомобиль')),
                    content_type='application/json')
            self.assertEqual(response.status_code, 200, response.content)
            self.assertIn('подходящим исполнителям', response.json()['message'])
            page = self.client.get(response.json()['result_url'])
            self.assertContains(page, 'подходящим исполнителям')
            self.assertNotContains(page, 'Подходящие исполнители пока не найдены')
        self.assertEqual(ServiceRequest.objects.count(), 2)
        self.assertEqual(mail.call_count, 2)
        self.assertFalse(ServiceRequestDispatch.objects.exists())
        http.assert_not_called()

    @patch('core.views.schedule_instagram_publication_for_request')
    @patch('core.services.operations_email_notifications.send_mail', return_value=1)
    @patch('core.whatsapp_template_sender.urllib.request.urlopen')
    def test_classic_parts_form_saves_and_emails(self, http, mail, instagram):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post('/api/create-request/', dict(brand='Toyota', model='Camry',
                city='Алматы', phone='77015556677', category='Тормоза', description='тормозные колодки'))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('Ваша заявка принята', response.json()['message'])
        self.assertEqual(Request.objects.count(), 1)
        self.assertFalse(RequestDispatch.objects.exists())
        mail.assert_called_once()
        http.assert_not_called()

    @patch('service_requests.services.whatsapp_dispatch._recover_stale_processing')
    @patch('service_requests.services.whatsapp_dispatch._process_dispatch')
    def test_service_worker_does_not_touch_old_queue(self, process, recover):
        self.assertEqual(process_due_service_request_dispatches()['sent'], 0)
        process.assert_not_called()
        recover.assert_not_called()
