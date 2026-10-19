import json
from django.test import TestCase
from django.utils import timezone
from marketing.models import MarketingWhatsAppTemplate
from marketing.services.templates.selectors import template_is_compatible_with_campaign
from core.whatsapp_redaction import redact_whatsapp_sensitive_data
from core.whatsapp_template_management import build_meta_template_payload
from service_requests.models import ServiceRequest, ServiceSeller, ServiceMatch
from service_requests.services.notification_template import TEMPLATE_NAME, TEMPLATE_BODY, TEMPLATE_BUTTONS
from service_requests.services.whatsapp_dispatch import service_request_template_call


class ServiceNotificationChainTests(TestCase):
    def setUp(self):
        self.template = MarketingWhatsAppTemplate.objects.create(
            name='Уведомление услуг', meta_template_name=TEMPLATE_NAME,
            language_code='ru', meta_status='approved', is_active=True,
            body_text=TEMPLATE_BODY, buttons=TEMPLATE_BUTTONS,
            variables=[dict(key=f'field_{i}', label=f'Поле {i}', example='Пример')
                       for i in range(1, 8)],
        )
        self.seller = ServiceSeller.objects.create(
            name='Тест', whatsapp='77001111111', city='Алматы', seller_type='sto',
            is_test_seller=True, test_all_requests_since=timezone.now(),
        )
        self.req = ServiceRequest.objects.create(
            service_type='sto', city='Алматы', brand='Jeep', model='Commander',
            phone='77002222222', description='x' * 1000,
        )
        ServiceMatch.objects.create(request=self.req, seller=self.seller)

    def test_approved_template_button_and_pages_for_both_directions(self):
        for direction, label in [('sto', 'СТО и ремонт'), ('detailing', 'Детейлинг и тюнинг')]:
            with self.subTest(direction=direction):
                self.req.service_type = direction
                self.req.save()
                call = service_request_template_call(self.req, self.seller)
                self.assertEqual(call['template_name'], TEMPLATE_NAME)
                texts = [p['text'] for p in call['body_parameters']]
                self.assertEqual(texts[1], label)
                self.assertEqual(texts[6], 'x' * 500)
                self.assertNotIn(self.req.phone, texts)
                suffix = call['button_components'][0]['parameters'][0]['text']
                path = '/service-request/personal/' + suffix
                self.assertTrue(suffix.endswith('/'))
                response = self.client.get(path)
                self.assertContains(response, label)
                self.assertContains(response, 'x' * 1000)
                self.assertContains(response, 'Написать клиенту в WhatsApp')
                self.assertContains(response, 'Позвонить клиенту')
                self.assertContains(response, 'Настроить фильтр получения заявок')
                filter_suffix = call['button_components'][1]['parameters'][0]['text']
                self.assertEqual(filter_suffix, suffix)
                self.assertEqual(self.client.get('/service-request/personal/filters/' + filter_suffix).status_code, 200)
                self.assertNotIn(suffix.rstrip('/'), json.dumps(redact_whatsapp_sensitive_data(call)))
                self.assertEqual(self.client.get(path + '?seller_id=999').context['req'].pk, self.req.pk)

    def test_pending_or_modified_contract_does_not_switch_sender(self):
        for fields in [dict(meta_status='pending'), dict(meta_status='approved', body_text='changed')]:
            MarketingWhatsAppTemplate.objects.filter(pk=self.template.pk).update(**fields)
            call = service_request_template_call(self.req, self.seller)
            self.assertNotEqual(call['template_name'], TEMPLATE_NAME)
            self.assertNotIn('button_components', call)

    def test_url_example_and_service_only_template(self):
        payload = build_meta_template_payload(self.template)
        buttons = next(c['buttons'] for c in payload['components'] if c['type'] == 'BUTTONS')
        self.assertEqual(buttons[0]['example'], ['https://zpt.kz/service-request/personal/example/'])
        self.assertEqual(buttons[1]['text'], 'Настроить фильтр')
        self.assertIn('example', buttons[1])
        self.template.allow_test_campaign = True
        self.assertFalse(template_is_compatible_with_campaign(self.template, purpose='test_campaign'))
        self.assertNotIn('автозапчасть', TEMPLATE_BODY)
        self.assertNotIn('поставки', TEMPLATE_BODY)
