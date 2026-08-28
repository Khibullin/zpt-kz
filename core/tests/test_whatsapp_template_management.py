from __future__ import annotations

import io
import json
from importlib import import_module
from unittest.mock import patch

import urllib.error

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.whatsapp_config import (
    WHATSAPP_CONFIG_INVALID_MESSAGE,
    WHATSAPP_TEMPLATE_CONFIG_INVALID_MESSAGE,
    WHATSAPP_WABA_ID_MISSING_MESSAGE,
    validate_whatsapp_sender_config,
    validate_whatsapp_template_management_config,
)
from core.whatsapp_template_management import (
    ALREADY_EXISTS_MESSAGE,
    CONTENT_MISMATCH_MESSAGE,
    build_meta_template_payload,
    map_meta_template_status,
    submit_whatsapp_template,
    sync_whatsapp_template_status,
)
from marketing.models import (
    MarketingCampaign,
    MarketingCampaignMessage,
    MarketingCampaignRecipient,
    MarketingCampaignSendRun,
    MarketingWhatsAppTemplate,
)
from marketing.services.templates.constants import (
    META_STATUS_APPROVED,
    META_STATUS_PENDING,
    META_STATUS_UNKNOWN,
)
from marketing.tests.test_marketing_templates import make_template


LAUNCH = import_module('marketing.migrations.0012_prepare_ag_parts_wholesale_launch')

FAKE_TOKEN = 'FAKE_TEST_WHATSAPP_ACCESS_TOKEN_abc123xyz'
FAKE_PHONE_ID = '123456789012345'
FAKE_WABA_ID = '102938475610293'
VALID_SENDER_ENV = {
    'WHATSAPP_PHONE_NUMBER_ID': FAKE_PHONE_ID,
    'WHATSAPP_ACCESS_TOKEN': FAKE_TOKEN,
}
VALID_TEMPLATE_ENV = {
    **VALID_SENDER_ENV,
    'WHATSAPP_BUSINESS_ACCOUNT_ID': FAKE_WABA_ID,
}


class FakeHTTPResponse:
    def __init__(self, payload, status=200):
        if isinstance(payload, (dict, list)):
            raw = json.dumps(payload, ensure_ascii=False)
        else:
            raw = str(payload)
        self._body = raw.encode('utf-8')
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _assert_no_token(test_case, value):
    dumped = json.dumps(value, default=str) if not isinstance(value, str) else value
    test_case.assertNotIn(FAKE_TOKEN, dumped)
    test_case.assertNotIn('Bearer ' + FAKE_TOKEN, dumped)


def _launch_campaign_untouched(test_case):
    campaign = MarketingCampaign.objects.get(name=LAUNCH.CAMPAIGN_NAME)
    test_case.assertEqual(campaign.status, 'draft')
    test_case.assertEqual(
        MarketingCampaignRecipient.objects.filter(campaign=campaign).count(),
        0,
    )
    test_case.assertEqual(
        MarketingCampaignSendRun.objects.filter(campaign=campaign).count(),
        0,
    )
    test_case.assertEqual(
        MarketingCampaignMessage.objects.filter(send_run__campaign=campaign).count(),
        0,
    )


def _ag_parts_template():
    return MarketingWhatsAppTemplate.objects.get(
        meta_template_name=LAUNCH.TEMPLATE_META_NAME,
        language_code=LAUNCH.TEMPLATE_LANGUAGE,
    )


def _meta_template_row(*, status='PENDING', body=None, header=None, footer=None, url=None):
    template = _ag_parts_template()
    header = template.header_text if header is None else header
    body = template.body_text if body is None else body
    footer = template.footer_text if footer is None else footer
    url = (template.buttons or [{}])[0].get('value') if url is None else url
    components = [
        {'type': 'HEADER', 'format': 'TEXT', 'text': header},
        {'type': 'BODY', 'text': body},
        {'type': 'FOOTER', 'text': footer},
        {
            'type': 'BUTTONS',
            'buttons': [{'type': 'URL', 'text': 'Оптовые цены', 'url': url}],
        },
    ]
    return {
        'id': '111222333',
        'name': template.meta_template_name,
        'language': template.language_code,
        'status': status,
        'category': 'MARKETING',
        'components': components,
    }


class WhatsAppTemplateConfigTests(TestCase):
    @patch.dict('os.environ', VALID_SENDER_ENV, clear=False)
    def test_sender_config_does_not_require_waba_id(self):
        with patch.dict('os.environ', {'WHATSAPP_BUSINESS_ACCOUNT_ID': ''}, clear=False):
            phone_id, token = validate_whatsapp_sender_config()
        self.assertEqual(phone_id, FAKE_PHONE_ID)
        self.assertEqual(token, FAKE_TOKEN)

    @patch.dict('os.environ', VALID_TEMPLATE_ENV, clear=False)
    def test_management_config_requires_waba_id(self):
        waba_id, token = validate_whatsapp_template_management_config()
        self.assertEqual(waba_id, FAKE_WABA_ID)
        self.assertEqual(token, FAKE_TOKEN)
        with patch.dict('os.environ', {'WHATSAPP_BUSINESS_ACCOUNT_ID': ''}, clear=False):
            with self.assertRaises(Exception) as ctx:
                validate_whatsapp_template_management_config()
        self.assertEqual(str(ctx.exception), WHATSAPP_WABA_ID_MISSING_MESSAGE)
        self.assertNotIn(FAKE_TOKEN, str(ctx.exception))

    @patch.dict(
        'os.environ',
        {**VALID_SENDER_ENV, 'WHATSAPP_BUSINESS_ACCOUNT_ID': 'not-digits'},
        clear=False,
    )
    def test_invalid_waba_id_safe_error(self):
        with self.assertRaises(Exception) as ctx:
            validate_whatsapp_template_management_config()
        self.assertEqual(str(ctx.exception), WHATSAPP_TEMPLATE_CONFIG_INVALID_MESSAGE)
        self.assertNotIn(FAKE_TOKEN, str(ctx.exception))
        self.assertNotEqual(str(ctx.exception), WHATSAPP_CONFIG_INVALID_MESSAGE)


class WhatsAppTemplatePayloadTests(TestCase):
    def test_ag_parts_payload_is_marketing_without_variables(self):
        template = _ag_parts_template()
        payload = build_meta_template_payload(template)
        self.assertEqual(payload['name'], 'zpt_ag_parts_wholesale_v1')
        self.assertEqual(payload['language'], 'ru')
        self.assertEqual(payload['category'], 'MARKETING')
        types = [item['type'] for item in payload['components']]
        self.assertEqual(types, ['HEADER', 'BODY', 'FOOTER', 'BUTTONS'])
        self.assertEqual(payload['components'][0]['format'], 'TEXT')
        self.assertEqual(payload['components'][0]['text'], template.header_text)
        self.assertEqual(payload['components'][1]['text'], template.body_text)
        self.assertEqual(payload['components'][2]['text'], template.footer_text)
        buttons = payload['components'][3]['buttons']
        self.assertEqual(buttons[0]['type'], 'URL')
        self.assertEqual(buttons[0]['text'], 'Оптовые цены')
        self.assertEqual(buttons[0]['url'], template.buttons[0]['value'])
        self.assertNotIn('value', buttons[0])
        dumped = json.dumps(payload)
        self.assertNotIn('"variables"', dumped)
        self.assertNotIn('"example"', dumped)

    def test_empty_header_footer_omitted(self):
        user = User.objects.create_user('tpl', password='secret12345', is_staff=True)
        template = make_template(
            user,
            header_text='',
            footer_text='',
            buttons=[],
            body_text='Только текст.',
        )
        payload = build_meta_template_payload(template)
        types = [item['type'] for item in payload['components']]
        self.assertEqual(types, ['BODY'])


@patch.dict('os.environ', VALID_TEMPLATE_ENV, clear=False)
class WhatsAppTemplateSubmitTests(TestCase):
    def test_pending_submit_posts_and_saves_id(self):
        template = _ag_parts_template()
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(request)
            self.assertEqual(timeout, 20)
            method = request.get_method()
            if method == 'GET':
                return FakeHTTPResponse({'data': []})
            self.assertEqual(method, 'POST')
            self.assertIn(f'/{FAKE_WABA_ID}/message_templates', request.full_url)
            self.assertNotIn('access_token=', request.full_url)
            body = json.loads(request.data.decode('utf-8'))
            self.assertEqual(body['category'], 'MARKETING')
            self.assertEqual(body['name'], template.meta_template_name)
            return FakeHTTPResponse({'id': '555666', 'status': 'PENDING', 'category': 'MARKETING'})

        with patch('core.whatsapp_template_management.urllib.request.urlopen', side_effect=fake_urlopen):
            result = submit_whatsapp_template(template)

        self.assertTrue(result.ok)
        self.assertTrue(result.created)
        self.assertEqual(result.meta_status, META_STATUS_PENDING)
        template.refresh_from_db()
        self.assertEqual(template.meta_template_id, '555666')
        self.assertEqual(template.meta_status, META_STATUS_PENDING)
        self.assertIsNotNone(template.last_status_checked_at)
        self.assertEqual([call.get_method() for call in calls], ['GET', 'POST'])
        _assert_no_token(self, result.message)
        _launch_campaign_untouched(self)

    def test_http_200_without_approved_status_does_not_approve(self):
        template = _ag_parts_template()

        def fake_urlopen(request, timeout=None):
            if request.get_method() == 'GET':
                return FakeHTTPResponse({'data': []})
            return FakeHTTPResponse({'id': '777', 'category': 'MARKETING'})

        with patch('core.whatsapp_template_management.urllib.request.urlopen', side_effect=fake_urlopen):
            result = submit_whatsapp_template(template)
        self.assertTrue(result.ok)
        template.refresh_from_db()
        self.assertEqual(template.meta_status, META_STATUS_UNKNOWN)
        self.assertNotEqual(template.meta_status, META_STATUS_APPROVED)
        self.assertEqual(template.meta_template_id, '777')
        _launch_campaign_untouched(self)

    def test_approved_only_when_meta_returns_approved(self):
        template = _ag_parts_template()

        def fake_urlopen(request, timeout=None):
            if request.get_method() == 'GET':
                return FakeHTTPResponse({'data': []})
            return FakeHTTPResponse({'id': '888', 'status': 'APPROVED'})

        with patch('core.whatsapp_template_management.urllib.request.urlopen', side_effect=fake_urlopen):
            result = submit_whatsapp_template(template)
        self.assertEqual(result.meta_status, META_STATUS_APPROVED)
        template.refresh_from_db()
        self.assertEqual(template.meta_status, META_STATUS_APPROVED)
        _launch_campaign_untouched(self)

    def test_http_error_redacts_token_and_does_not_approve(self):
        template = _ag_parts_template()
        error_body = json.dumps({
            'error': {
                'message': f'Invalid OAuth access token - {FAKE_TOKEN}',
                'code': 190,
            },
        })

        def fake_urlopen(request, timeout=None):
            if request.get_method() == 'GET':
                return FakeHTTPResponse({'data': []})
            raise urllib.error.HTTPError(
                url=request.full_url,
                code=400,
                msg='Bad Request',
                hdrs=None,
                fp=io.BytesIO(error_body.encode('utf-8')),
            )

        with self.assertLogs('core.whatsapp_template_management', level='WARNING') as logs:
            with patch('core.whatsapp_template_management.urllib.request.urlopen', side_effect=fake_urlopen):
                result = submit_whatsapp_template(template)
        self.assertFalse(result.ok)
        template.refresh_from_db()
        self.assertNotEqual(template.meta_status, META_STATUS_APPROVED)
        _assert_no_token(self, result.message)
        _assert_no_token(self, result.error)
        self.assertNotIn(FAKE_TOKEN, '\n'.join(logs.output))
        _launch_campaign_untouched(self)

    def test_existing_identical_template_skips_post(self):
        template = _ag_parts_template()
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(request.get_method())
            if request.get_method() == 'POST':
                raise AssertionError('POST must not be called for identical existing template')
            return FakeHTTPResponse({'data': [_meta_template_row(status='PENDING')]})

        with patch('core.whatsapp_template_management.urllib.request.urlopen', side_effect=fake_urlopen):
            result = submit_whatsapp_template(template)
        self.assertTrue(result.ok)
        self.assertTrue(result.already_exists)
        self.assertEqual(result.message, ALREADY_EXISTS_MESSAGE)
        self.assertEqual(calls, ['GET'])
        template.refresh_from_db()
        self.assertEqual(template.meta_template_id, '111222333')
        self.assertEqual(template.meta_status, META_STATUS_PENDING)
        _launch_campaign_untouched(self)

    def test_existing_different_template_blocks_without_post(self):
        template = _ag_parts_template()
        calls = []
        row = _meta_template_row(body='Другой текст')

        def fake_urlopen(request, timeout=None):
            calls.append(request.get_method())
            if request.get_method() == 'POST':
                raise AssertionError('POST must not be called on content mismatch')
            return FakeHTTPResponse({'data': [row]})

        with patch('core.whatsapp_template_management.urllib.request.urlopen', side_effect=fake_urlopen):
            result = submit_whatsapp_template(template)
        self.assertFalse(result.ok)
        self.assertTrue(result.conflict)
        self.assertEqual(result.message, CONTENT_MISMATCH_MESSAGE)
        self.assertEqual(calls, ['GET'])
        template.refresh_from_db()
        self.assertEqual(template.meta_status, META_STATUS_UNKNOWN)
        _launch_campaign_untouched(self)

    def test_status_sync_not_found_and_approved(self):
        template = _ag_parts_template()
        with patch(
            'core.whatsapp_template_management.urllib.request.urlopen',
            return_value=FakeHTTPResponse({'data': []}),
        ):
            missing = sync_whatsapp_template_status(template)
        self.assertTrue(missing.ok)
        self.assertTrue(missing.not_found)
        template.refresh_from_db()
        self.assertEqual(template.meta_template_id, '')
        self.assertEqual(template.meta_status, META_STATUS_UNKNOWN)

        with patch(
            'core.whatsapp_template_management.urllib.request.urlopen',
            return_value=FakeHTTPResponse({'data': [_meta_template_row(status='APPROVED')]}),
        ):
            approved = sync_whatsapp_template_status(template)
        self.assertEqual(approved.meta_status, META_STATUS_APPROVED)
        template.refresh_from_db()
        self.assertEqual(template.meta_status, META_STATUS_APPROVED)
        _launch_campaign_untouched(self)

    def test_map_unknown_meta_status(self):
        self.assertEqual(map_meta_template_status('FLAGGED'), META_STATUS_UNKNOWN)
        self.assertEqual(map_meta_template_status('PENDING'), META_STATUS_PENDING)


@patch.dict('os.environ', VALID_TEMPLATE_ENV, clear=False)
class WhatsAppTemplateAdminTests(TestCase):
    def setUp(self):
        self.template = _ag_parts_template()
        self.submit_url = reverse(
            'admin:marketing_marketingwhatsapptemplate_submit_to_meta',
            args=[self.template.pk],
        )
        self.status_url = reverse(
            'admin:marketing_marketingwhatsapptemplate_check_meta_status',
            args=[self.template.pk],
        )
        self.admin_user = User.objects.create_superuser(
            'meta-admin',
            'meta-admin@test.local',
            'secret12345',
        )

    def test_nonstaff_forbidden(self):
        anonymous = self.client.get(self.submit_url)
        self.assertEqual(anonymous.status_code, 302)
        user = User.objects.create_user('plain', password='secret12345')
        self.client.force_login(user)
        forbidden = self.client.get(self.submit_url)
        self.assertIn(forbidden.status_code, (302, 403))
        _launch_campaign_untouched(self)

    def test_get_cannot_submit(self):
        self.client.force_login(self.admin_user)
        with patch('core.whatsapp_template_management.urllib.request.urlopen') as mocked:
            response = self.client.get(self.submit_url)
        mocked.assert_not_called()
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        self.assertIn('zpt_ag_parts_wholesale_v1', html)
        self.assertIn('MARKETING', html)
        self.assertIn('НЕ отправляет сообщения получателям', html)
        self.assertIn('Подтвердить отправку шаблона в Meta', html)
        self.template.refresh_from_db()
        self.assertEqual(self.template.meta_status, META_STATUS_UNKNOWN)
        _launch_campaign_untouched(self)

    def test_post_without_confirm_does_not_call_meta(self):
        self.client.force_login(self.admin_user)
        with patch('core.whatsapp_template_management.urllib.request.urlopen') as mocked:
            response = self.client.post(self.submit_url)
        mocked.assert_not_called()
        self.assertEqual(response.status_code, 200)
        _launch_campaign_untouched(self)

    def test_missing_waba_id_safe_message(self):
        self.client.force_login(self.admin_user)
        with patch.dict('os.environ', {'WHATSAPP_BUSINESS_ACCOUNT_ID': ''}, clear=False):
            with patch('core.whatsapp_template_management.urllib.request.urlopen') as mocked:
                response = self.client.post(self.submit_url, {'confirm': '1'}, follow=True)
        mocked.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, WHATSAPP_WABA_ID_MISSING_MESSAGE)
        self.assertNotContains(response, FAKE_TOKEN)
        _launch_campaign_untouched(self)

    def test_confirmed_post_submits_and_status_check_works(self):
        self.client.force_login(self.admin_user)

        def fake_urlopen(request, timeout=None):
            if request.get_method() == 'GET':
                return FakeHTTPResponse({'data': []})
            return FakeHTTPResponse({'id': '999', 'status': 'PENDING'})

        with patch('core.whatsapp_template_management.urllib.request.urlopen', side_effect=fake_urlopen):
            response = self.client.post(self.submit_url, {'confirm': '1'})
        self.assertEqual(response.status_code, 302)
        self.template.refresh_from_db()
        self.assertEqual(self.template.meta_status, META_STATUS_PENDING)
        self.assertEqual(self.template.meta_template_id, '999')

        with patch(
            'core.whatsapp_template_management.urllib.request.urlopen',
            return_value=FakeHTTPResponse({'data': [_meta_template_row(status='APPROVED')]}),
        ):
            status_response = self.client.post(self.status_url)
        self.assertEqual(status_response.status_code, 302)
        self.template.refresh_from_db()
        self.assertEqual(self.template.meta_status, META_STATUS_APPROVED)
        _launch_campaign_untouched(self)

    def test_change_page_has_meta_buttons(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse(
                'admin:marketing_marketingwhatsapptemplate_change',
                args=[self.template.pk],
            )
        )
        self.assertContains(response, 'Отправить в Meta на согласование')
        self.assertContains(response, 'Проверить статус в Meta')
        _launch_campaign_untouched(self)
