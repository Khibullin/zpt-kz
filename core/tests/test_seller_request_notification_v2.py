import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Request, RequestDispatch, Seller, SellerRequestAccess
from core.request_dispatch_service import send_single_dispatch
from core.services.seller_request_access import seller_request_whatsapp_url_suffix
from core.views import (
    CURRENT_SELLER_REQUEST_TEMPLATE_NAME,
    _buyer_template_body_params,
    _seller_consent_template_body_params,
    _seller_request_url_button_components,
    _seller_template_body_params,
    build_seller_request_send_kwargs,
    resolve_seller_request_notification_template_name,
    send_whatsapp_template,
)
from marketing.services.templates.constants import (
    BASE_RESERVED_SERVICE_TEMPLATE_NAMES,
    get_reserved_service_template_names,
)


def _make_request(**overrides):
    data = {
        'transport_type': 'car',
        'brand': 'Toyota',
        'model': 'Camry 2018',
        'category': 'Тормозная система',
        'city': 'Алматы',
        'description': 'Нужен задний тормозной цилиндр, желательно оригинал.',
        'phone': '77001112233',
        'status': 'sent',
    }
    data.update(overrides)
    return Request.objects.create(**data)


def _make_seller():
    return Seller.objects.create(
        name='V2 template seller',
        whatsapp='77015550001',
        transport_type='car',
        city='Алматы',
        is_active=True,
        is_paused=False,
        receive_requests=True,
    )


class SellerRequestNotificationV2Tests(TestCase):
    def setUp(self):
        self.req = _make_request()
        self.seller = _make_seller()

    def test_template_name_is_v2(self):
        self.assertEqual(
            resolve_seller_request_notification_template_name(),
            'zpt_request_notification_v2',
        )
        self.assertEqual(
            CURRENT_SELLER_REQUEST_TEMPLATE_NAME,
            'zpt_request_notification_v2',
        )

    @override_settings(WHATSAPP_TEMPLATE_NAME='zpt_request_notification')
    def test_legacy_env_name_maps_to_v2(self):
        self.assertEqual(
            resolve_seller_request_notification_template_name(),
            'zpt_request_notification_v2',
        )

    @override_settings(WHATSAPP_TEMPLATE_NAME='mp_request_v1')
    def test_legacy_mp_request_name_maps_to_v2(self):
        self.assertEqual(
            resolve_seller_request_notification_template_name(),
            'zpt_request_notification_v2',
        )

    def test_body_has_six_parameters_in_order(self):
        params = _seller_template_body_params(self.req)
        self.assertEqual(len(params), 6)
        self.assertEqual(params[0]['text'], str(self.req.id))
        self.assertEqual(params[1]['text'], 'Toyota')
        self.assertEqual(params[2]['text'], 'Camry 2018')
        self.assertEqual(params[3]['text'], 'Тормозная система')
        self.assertEqual(params[4]['text'], 'Алматы')
        self.assertEqual(
            params[5]['text'],
            'Нужен задний тормозной цилиндр, желательно оригинал.',
        )
        self.assertNotIn('/r/', params[5]['text'])
        self.assertNotIn('https://zpt.kz', params[5]['text'])

    def test_missing_comment_stays_valid(self):
        req = _make_request(description='')
        params = _seller_template_body_params(req)
        self.assertEqual(len(params), 6)
        self.assertEqual(params[5]['text'], '-')
        for item in params:
            self.assertEqual(item['type'], 'text')
            self.assertTrue(item['text'])

    def test_button_suffix_is_token_only(self):
        suffix = seller_request_whatsapp_url_suffix('demo431x7k9p2')
        self.assertEqual(suffix, 'demo431x7k9p2/')
        self.assertFalse(suffix.startswith('/sr/'))
        self.assertNotIn('https://', suffix)
        self.assertNotIn('zpt.kz', suffix)

        components = _seller_request_url_button_components('demo431x7k9p2')
        self.assertEqual(len(components), 1)
        button = components[0]
        self.assertEqual(button['type'], 'button')
        self.assertEqual(button['sub_type'], 'url')
        self.assertEqual(button['index'], '0')
        self.assertEqual(button['parameters'][0]['text'], 'demo431x7k9p2/')

    def test_send_kwargs_create_per_seller_token_and_v2_payload(self):
        kwargs = build_seller_request_send_kwargs(self.req, self.seller)
        self.assertEqual(kwargs['template_name'], 'zpt_request_notification_v2')
        self.assertEqual(len(kwargs['body_parameters']), 6)
        self.assertEqual(len(kwargs['button_components']), 1)
        self.assertFalse(kwargs['include_image_header'])

        access = SellerRequestAccess.objects.get(request=self.req, seller=self.seller)
        suffix = kwargs['button_components'][0]['parameters'][0]['text']
        self.assertEqual(suffix, f'{access.token}/')
        self.assertNotIn('https://', suffix)
        self.assertNotIn('/sr/', suffix)
        self.assertTrue(suffix.endswith('/'))

    @override_settings(WHATSAPP_TEMPLATE_LANG='ru')
    @patch.dict(
        'os.environ',
        {
            'WHATSAPP_PHONE_NUMBER_ID': '123456789',
            'WHATSAPP_ACCESS_TOKEN': 'test-token',
        },
        clear=False,
    )
    @patch('core.whatsapp_template_sender.urllib.request.urlopen')
    def test_send_payload_language_body_and_button(self, mocked_urlopen):
        mocked_urlopen.return_value.__enter__.return_value.status = 200
        mocked_urlopen.return_value.__enter__.return_value.read.return_value = (
            b'{"messages":[{"id":"wamid.v2"}]}'
        )
        kwargs = build_seller_request_send_kwargs(self.req, self.seller)

        result = send_whatsapp_template(
            self.seller.whatsapp,
            self.req,
            self.seller.name,
            **kwargs,
        )
        self.assertTrue(result['ok'])

        payload = json.loads(mocked_urlopen.call_args[0][0].data.decode('utf-8'))
        template = payload['template']
        self.assertEqual(template['name'], 'zpt_request_notification_v2')
        self.assertEqual(template['language']['code'], 'ru')

        body = next(c for c in template['components'] if c['type'] == 'body')
        buttons = [c for c in template['components'] if c['type'] == 'button']
        self.assertEqual(len(body['parameters']), 6)
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0]['sub_type'], 'url')
        self.assertEqual(buttons[0]['index'], '0')
        self.assertNotIn('https://', buttons[0]['parameters'][0]['text'])
        self.assertNotIn('?q=', json.dumps(payload))

    @patch('core.views.send_whatsapp_template', return_value={'ok': True})
    def test_dispatch_uses_v2_kwargs(self, mocked_send):
        dispatch = RequestDispatch.objects.create(
            request=self.req,
            seller=self.seller,
            wave_number=1,
            position_number=1,
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at=timezone.now(),
        )
        result = send_single_dispatch(dispatch)
        self.assertTrue(result['ok'])
        kwargs = mocked_send.call_args.kwargs
        self.assertEqual(kwargs['template_name'], 'zpt_request_notification_v2')
        self.assertEqual(len(kwargs['body_parameters']), 6)
        self.assertEqual(kwargs['button_components'][0]['sub_type'], 'url')

    def test_other_templates_unchanged(self):
        buyer_params = _buyer_template_body_params(self.req, sellers_count=3)
        self.assertEqual(len(buyer_params), 5)
        consent_params = _seller_consent_template_body_params(self.req)
        self.assertEqual(len(consent_params), 7)
        reserved = get_reserved_service_template_names()
        self.assertIn('zpt_request_notification_v2', reserved)
        self.assertIn('zpt_request_notification', reserved)
        self.assertIn('zpt_buyer_request_receipt', reserved)
        self.assertIn('mp_request_v1', reserved)
        self.assertIn('zpt_buyer_request_receipt', BASE_RESERVED_SERVICE_TEMPLATE_NAMES)

    def test_example_payload_matches_approved_template(self):
        req = _make_request(pk=431)
        body = _seller_template_body_params(req)
        button = _seller_request_url_button_components('demo431x7k9p2')
        payload = {
            'messaging_product': 'whatsapp',
            'to': '77015550001',
            'type': 'template',
            'template': {
                'name': 'zpt_request_notification_v2',
                'language': {'code': 'ru'},
                'components': [
                    {'type': 'body', 'parameters': body},
                    *button,
                ],
            },
        }
        texts = [item['text'] for item in body]
        self.assertEqual(
            texts,
            [
                '431',
                'Toyota',
                'Camry 2018',
                'Тормозная система',
                'Алматы',
                'Нужен задний тормозной цилиндр, желательно оригинал.',
            ],
        )
        self.assertEqual(button[0]['parameters'][0]['text'], 'demo431x7k9p2/')
        self.assertNotIn('https://zpt.kz/sr/demo431x7k9p2/', json.dumps(payload))
        self.assertEqual(
            f"https://zpt.kz/sr/{button[0]['parameters'][0]['text']}",
            'https://zpt.kz/sr/demo431x7k9p2/',
        )
        self.example_payload = payload
        self.assertEqual(payload['template']['name'], 'zpt_request_notification_v2')
        self.assertEqual(payload['template']['language']['code'], 'ru')
