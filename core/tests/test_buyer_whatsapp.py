from unittest.mock import patch
import json

from django.test import TestCase, override_settings

from core.buyer_portal import (
    buyer_history_whatsapp_url_suffix,
    buyer_request_whatsapp_url_suffix,
    ensure_buyer_portal_access,
)
from core.models import BuyerPortalAccess, Request
from core.views import (
    _buyer_template_body_params,
    _buyer_template_button_components,
    send_whatsapp_template,
)


class BuyerWhatsAppTemplateTests(TestCase):
    def setUp(self):
        self.phone = '77001112233'
        self.req = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Camry',
            category='Тормоза',
            city='Алматы',
            phone=self.phone,
            status='sent',
        )
        self.portal = ensure_buyer_portal_access(self.phone)
        self.sellers_count = 4

    def test_buyer_template_body_params_returns_five_values_in_order(self):
        params = _buyer_template_body_params(self.req, sellers_count=self.sellers_count)

        self.assertEqual(len(params), 5)
        self.assertEqual(params[0]['text'], str(self.req.id))
        self.assertEqual(params[1]['text'], 'Toyota Camry')
        self.assertEqual(params[2]['text'], 'Тормоза')
        self.assertEqual(params[3]['text'], 'Алматы')
        self.assertEqual(params[4]['text'], str(self.sellers_count))

    def test_buyer_template_button_components_include_two_url_buttons(self):
        components = _buyer_template_button_components(self.req)

        self.assertEqual(len(components), 2)
        self.assertEqual(components[0]['type'], 'button')
        self.assertEqual(components[0]['sub_type'], 'url')
        self.assertEqual(components[0]['index'], '0')
        self.assertEqual(components[1]['type'], 'button')
        self.assertEqual(components[1]['sub_type'], 'url')
        self.assertEqual(components[1]['index'], '1')

    def test_request_button_suffix_uses_request_id_and_access_token(self):
        suffix = buyer_request_whatsapp_url_suffix(self.req)

        self.assertEqual(suffix, f'{self.req.id}/{self.req.access_token}/')
        self.assertEqual(
            _buyer_template_button_components(self.req)[0]['parameters'][0]['text'],
            suffix,
        )

    def test_history_button_suffix_uses_buyer_portal_access_token(self):
        suffix = buyer_history_whatsapp_url_suffix(self.req)

        self.assertEqual(suffix, f'{self.portal.access_token}/')
        self.assertEqual(
            _buyer_template_button_components(self.req)[1]['parameters'][0]['text'],
            suffix,
        )

    @override_settings(
        WHATSAPP_BUYER_TEMPLATE_NAME='zpt_buyer_request_receipt',
        WHATSAPP_TEMPLATE_LANG='ru',
    )
    @patch.dict(
        'os.environ',
        {
            'WHATSAPP_PHONE_NUMBER_ID': '123456789',
            'WHATSAPP_ACCESS_TOKEN': 'test-token',
        },
        clear=False,
    )
    @patch('core.views.urllib.request.urlopen')
    def test_send_buyer_whatsapp_payload_includes_body_and_buttons(
        self,
        mock_urlopen,
    ):
        mock_response = mock_urlopen.return_value.__enter__.return_value
        mock_response.status = 200
        mock_response.read.return_value = b'{"messages":[{"id":"wamid.test"}]}'

        result = send_whatsapp_template(
            self.phone,
            self.req,
            'Покупатель',
            template_name='zpt_buyer_request_receipt',
            body_parameters=_buyer_template_body_params(
                self.req,
                sellers_count=self.sellers_count,
            ),
            button_components=_buyer_template_button_components(self.req),
            include_image_header=False,
        )

        self.assertTrue(result['ok'])
        request_obj = mock_urlopen.call_args[0][0]
        payload = json.loads(request_obj.data.decode('utf-8'))
        template = payload['template']

        self.assertEqual(template['name'], 'zpt_buyer_request_receipt')
        self.assertEqual(template['language']['code'], 'ru')

        body_component = next(
            component for component in template['components']
            if component['type'] == 'body'
        )
        self.assertEqual(len(body_component['parameters']), 5)

        button_components = [
            component for component in template['components']
            if component['type'] == 'button'
        ]
        self.assertEqual(len(button_components), 2)
        self.assertEqual(
            button_components[0]['parameters'][0]['text'],
            f'{self.req.id}/{self.req.access_token}/',
        )
        self.assertEqual(
            button_components[1]['parameters'][0]['text'],
            f'{self.portal.access_token}/',
        )

    def test_history_suffix_uses_existing_portal_token(self):
        existing = BuyerPortalAccess.objects.create(
            phone_normalized='77005556677',
        )
        other_req = Request.objects.create(
            transport_type='car',
            brand='BMW',
            model='X5',
            category='Двигатель',
            city='Астана',
            phone='77005556677',
            status='sent',
        )

        suffix = buyer_history_whatsapp_url_suffix(other_req)

        self.assertEqual(suffix, f'{existing.access_token}/')
