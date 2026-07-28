from __future__ import annotations

import io
import json
import uuid
from unittest.mock import patch

import urllib.error

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.whatsapp_config import (
    WHATSAPP_CONFIG_INVALID_CODE,
    WHATSAPP_CONFIG_INVALID_MESSAGE,
    validate_whatsapp_sender_config,
)
from core.whatsapp_redaction import redact_whatsapp_sensitive_data
from core.whatsapp_template_sender import send_whatsapp_template_message
from marketing.admin import MarketingCampaignMessageAdmin
from marketing.models import (
    MarketingCampaignMessage,
    MarketingCampaignRecipient,
    MarketingCampaignSendRun,
)
from marketing.services.campaigns.constants import (
    CHANNEL_WHATSAPP,
    ELIGIBILITY_ELIGIBLE,
    STATUS_AUDIENCE_PREPARED,
)
from marketing.services.campaigns.live_processor import process_marketing_live_send_batch
from marketing.services.campaigns.send_constants import (
    ACTIVE_SIMPLE_MAILING_LOCK_VALUE,
    MESSAGE_STATUS_FAILED,
    MESSAGE_STATUS_PROCESSING,
    MESSAGE_STATUS_QUEUED,
    MESSAGE_STATUS_SENT,
    SEND_MODE_LIVE,
    SEND_RUN_STATUS_QUEUED,
    WORKFLOW_TYPE_SIMPLE_MAILING,
)
from marketing.services.campaigns.test_send import _extract_meta_error, execute_test_campaign_send
from marketing.services.simple_mailing.constants import RECIPIENT_SCOPE_CONTROL_ONLY
from marketing.services.simple_mailing.launch import launch_simple_mailing
from marketing.tests.test_marketing_audiences import grant_consent, grant_marketing_permission, make_buyer
from marketing.tests.test_marketing_campaign_live_send import LIVE_SETTINGS
from marketing.tests.test_marketing_campaign_send import ensure_portal_access, make_test_send_template, setup_ready_test_campaign
from marketing.tests.test_marketing_campaigns import make_audience, make_campaign
from marketing.tests.test_marketing_simple_mailing import make_request


FAKE_TOKEN = 'FAKE_TEST_WHATSAPP_ACCESS_TOKEN_abc123xyz'
FAKE_PHONE_ID = '123456789012345'
VALID_ENV = {
    'WHATSAPP_PHONE_NUMBER_ID': FAKE_PHONE_ID,
    'WHATSAPP_ACCESS_TOKEN': FAKE_TOKEN,
}

LIVE_SIMPLE_SETTINGS = {
    **LIVE_SETTINGS,
    'MARKETING_SIMPLE_WAVE_SIZE': 10,
    'MARKETING_SIMPLE_WAVE_INTERVAL_MINUTES': 5,
    'MARKETING_LIVE_SEND_INTERVAL_SECONDS': 0,
}


def _body_parameters():
    return [{'type': 'text', 'text': 'hello'}]


class WhatsAppConfigValidationTests(TestCase):
    @patch.dict('os.environ', {'WHATSAPP_PHONE_NUMBER_ID': 'not-a-number', 'WHATSAPP_ACCESS_TOKEN': FAKE_TOKEN})
    def test_non_numeric_phone_number_id_rejected(self):
        with self.assertRaises(Exception):
            validate_whatsapp_sender_config()

    @patch.dict('os.environ', {'WHATSAPP_PHONE_NUMBER_ID': FAKE_TOKEN, 'WHATSAPP_ACCESS_TOKEN': FAKE_TOKEN})
    def test_phone_number_id_must_not_equal_token(self):
        with self.assertRaises(Exception):
            validate_whatsapp_sender_config()

    @patch.dict('os.environ', {'WHATSAPP_PHONE_NUMBER_ID': '', 'WHATSAPP_ACCESS_TOKEN': FAKE_TOKEN})
    def test_missing_phone_number_id_rejected(self):
        with self.assertRaises(Exception):
            validate_whatsapp_sender_config()

    @patch.dict('os.environ', {'WHATSAPP_PHONE_NUMBER_ID': FAKE_PHONE_ID, 'WHATSAPP_ACCESS_TOKEN': ''})
    def test_missing_access_token_rejected(self):
        with self.assertRaises(Exception):
            validate_whatsapp_sender_config()


@patch.dict('os.environ', VALID_ENV, clear=False)
class WhatsAppSenderSecurityTests(TestCase):
    @patch('core.whatsapp_template_sender.urllib.request.urlopen')
    def test_invalid_phone_number_id_blocks_before_http(self, mocked_urlopen):
        with patch.dict('os.environ', {
            'WHATSAPP_PHONE_NUMBER_ID': 'not-a-number',
            'WHATSAPP_ACCESS_TOKEN': FAKE_TOKEN,
        }):
            result = send_whatsapp_template_message(
                '77001234567',
                template_name='test_template',
                body_parameters=_body_parameters(),
            )
        mocked_urlopen.assert_not_called()
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_code'], WHATSAPP_CONFIG_INVALID_CODE)
        self.assertEqual(result['error'], WHATSAPP_CONFIG_INVALID_MESSAGE)
        self.assertNotIn(FAKE_TOKEN, json.dumps(result))

    @patch('core.whatsapp_template_sender.urllib.request.urlopen')
    def test_phone_number_id_equal_token_blocks_before_http(self, mocked_urlopen):
        with patch.dict('os.environ', {
            'WHATSAPP_PHONE_NUMBER_ID': FAKE_TOKEN,
            'WHATSAPP_ACCESS_TOKEN': FAKE_TOKEN,
        }):
            result = send_whatsapp_template_message(
                '77001234567',
                template_name='test_template',
                body_parameters=_body_parameters(),
            )
        mocked_urlopen.assert_not_called()
        self.assertEqual(result['error_code'], WHATSAPP_CONFIG_INVALID_CODE)

    @patch('core.whatsapp_template_sender.urllib.request.urlopen')
    def test_missing_phone_number_id_blocks_before_http(self, mocked_urlopen):
        with patch.dict('os.environ', {'WHATSAPP_PHONE_NUMBER_ID': '', 'WHATSAPP_ACCESS_TOKEN': FAKE_TOKEN}):
            result = send_whatsapp_template_message(
                '77001234567',
                template_name='test_template',
                body_parameters=_body_parameters(),
            )
        mocked_urlopen.assert_not_called()
        self.assertEqual(result['error_code'], WHATSAPP_CONFIG_INVALID_CODE)

    @patch('core.whatsapp_template_sender.urllib.request.urlopen')
    def test_missing_access_token_blocks_before_http(self, mocked_urlopen):
        with patch.dict('os.environ', {'WHATSAPP_PHONE_NUMBER_ID': FAKE_PHONE_ID, 'WHATSAPP_ACCESS_TOKEN': ''}):
            result = send_whatsapp_template_message(
                '77001234567',
                template_name='test_template',
                body_parameters=_body_parameters(),
            )
        mocked_urlopen.assert_not_called()
        self.assertEqual(result['error_code'], WHATSAPP_CONFIG_INVALID_CODE)

    @patch('core.whatsapp_template_sender.urllib.request.urlopen')
    def test_http_error_body_redacts_access_token(self, mocked_urlopen):
        error_body = json.dumps({
            'error': {
                'message': f'Invalid OAuth access token - {FAKE_TOKEN}',
                'type': 'OAuthException',
                'code': 190,
            },
        })
        mocked_urlopen.side_effect = urllib.error.HTTPError(
            url='https://graph.facebook.com/v20.0/messages',
            code=400,
            msg='Bad Request',
            hdrs=None,
            fp=io.BytesIO(error_body.encode('utf-8')),
        )
        result = send_whatsapp_template_message(
            '77001234567',
            template_name='test_template',
            body_parameters=_body_parameters(),
        )
        self.assertFalse(result['ok'])
        payload = json.dumps(result)
        self.assertNotIn(FAKE_TOKEN, payload)
        self.assertIn('[REDACTED]', payload)

    def test_extract_meta_error_parses_json_string(self):
        raw = json.dumps({
            'error': {
                'message': 'Invalid parameter',
                'type': 'OAuthException',
                'code': 100,
                'error_subcode': 33,
            },
        })
        error_code, error_message = _extract_meta_error({'error': raw})
        self.assertEqual(error_code, '100')
        self.assertIn('Invalid parameter', error_message)
        self.assertIn('subcode=33', error_message)
        self.assertNotIn(FAKE_TOKEN, error_message)

    @patch('core.whatsapp_template_sender.urllib.request.urlopen')
    def test_generic_exception_result_is_sanitized(self, mocked_urlopen):
        mocked_urlopen.side_effect = RuntimeError(f'boom Bearer {FAKE_TOKEN}')
        result = send_whatsapp_template_message(
            '77001234567',
            template_name='test_template',
            body_parameters=_body_parameters(),
        )
        self.assertFalse(result['ok'])
        self.assertNotIn(FAKE_TOKEN, str(result['error']))
        self.assertIn('[REDACTED]', str(result['error']))


class WhatsAppRedactionTests(TestCase):
    @patch.dict('os.environ', VALID_ENV, clear=False)
    def test_redact_exact_configured_token(self):
        text = f'failed with token {FAKE_TOKEN} in body'
        self.assertEqual(
            redact_whatsapp_sensitive_data(text),
            'failed with token [REDACTED] in body',
        )

    @patch.dict('os.environ', VALID_ENV, clear=False)
    def test_redact_bearer_header(self):
        text = f'Authorization: Bearer {FAKE_TOKEN}'
        self.assertEqual(
            redact_whatsapp_sensitive_data(text),
            'Authorization: Bearer [REDACTED]',
        )

    @patch.dict('os.environ', VALID_ENV, clear=False)
    def test_redact_access_token_query(self):
        text = f'https://graph.facebook.com?access_token={FAKE_TOKEN}&fields=id'
        sanitized = redact_whatsapp_sensitive_data(text)
        self.assertNotIn(FAKE_TOKEN, sanitized)
        self.assertIn('access_token=[REDACTED]', sanitized)


def _make_control_buyer():
    buyer = make_buyer(is_control_recipient=True, is_test_contact=False)
    make_request(buyer, brand='Toyota')
    grant_consent(buyer)
    ensure_portal_access(buyer)
    return buyer


def _launch_draft(*, count: int, template_id: int) -> dict:
    return {
        'recipient_type': 'parts_request_buyers',
        'recipient_scope': RECIPIENT_SCOPE_CONTROL_ONLY,
        'all_brands': True,
        'brands': [],
        'count': count,
        'template_id': template_id,
    }


@override_settings(**LIVE_SIMPLE_SETTINGS)
class LiveProcessorWhatsAppConfigPreflightTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('wa-sec', password='secret', is_staff=True)

    def _launch_control_run(self) -> MarketingCampaignSendRun:
        _make_control_buyer()
        template = make_test_send_template(self.user)
        result = launch_simple_mailing(
            draft=_launch_draft(count=1, template_id=template.pk),
            template=template,
            created_by=self.user,
            launch_key=str(uuid.uuid4()),
        )
        return MarketingCampaignSendRun.objects.get(pk=result.send_run_id)

    @patch.dict('os.environ', {'WHATSAPP_PHONE_NUMBER_ID': 'bad-id', 'WHATSAPP_ACCESS_TOKEN': FAKE_TOKEN})
    def test_invalid_config_keeps_messages_queued(self):
        send_run = self._launch_control_run()
        self.assertEqual(send_run.active_simple_mailing_lock, ACTIVE_SIMPLE_MAILING_LOCK_VALUE)

        batch = process_marketing_live_send_batch()

        send_run.refresh_from_db()
        self.assertEqual(batch.processed_count, 0)
        self.assertEqual(batch.sent_count, 0)
        self.assertEqual(batch.failed_count, 0)
        self.assertEqual(batch.skipped_count, 0)
        self.assertEqual(batch.remaining_queued, 1)
        self.assertEqual(
            send_run.messages.filter(status=MESSAGE_STATUS_QUEUED).count(),
            1,
        )
        self.assertEqual(
            send_run.messages.filter(status=MESSAGE_STATUS_PROCESSING).count(),
            0,
        )
        self.assertEqual(
            send_run.messages.filter(status=MESSAGE_STATUS_FAILED).count(),
            0,
        )
        self.assertEqual(send_run.active_simple_mailing_lock, ACTIVE_SIMPLE_MAILING_LOCK_VALUE)

    @patch.dict('os.environ', {'WHATSAPP_PHONE_NUMBER_ID': 'bad-id', 'WHATSAPP_ACCESS_TOKEN': FAKE_TOKEN})
    def test_queued_run_processes_after_config_fixed(self):
        send_run = self._launch_control_run()
        process_marketing_live_send_batch()
        send_run.refresh_from_db()
        self.assertEqual(send_run.messages.filter(status=MESSAGE_STATUS_QUEUED).count(), 1)

        def mock_send_ok(*args, **kwargs):
            return {
                'ok': True,
                'status_code': 200,
                'message_id': 'wamid.security.test',
                'error': None,
            }

        with patch.dict('os.environ', VALID_ENV, clear=False):
            with patch(
                'marketing.services.campaigns.live_processor.send_whatsapp_template_message',
                side_effect=mock_send_ok,
            ):
                batch = process_marketing_live_send_batch()
        self.assertEqual(batch.sent_count, 1)
        self.assertEqual(
            send_run.messages.filter(status=MESSAGE_STATUS_SENT).count(),
            1,
        )

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_injected_mock_does_not_require_production_env(self, mocked_send):
        mocked_send.return_value = {
            'ok': True,
            'status_code': 200,
            'message_id': 'wamid.mock.test',
            'error': None,
        }
        with patch.dict('os.environ', {}, clear=True):
            send_run = self._launch_control_run()
            batch = process_marketing_live_send_batch()
        self.assertEqual(batch.sent_count, 1)
        self.assertEqual(
            send_run.messages.filter(status=MESSAGE_STATUS_SENT).count(),
            1,
        )


class MarketingTestSendPersistedErrorSanitizationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('test-send-sec', password='secret', is_staff=True)
        grant_marketing_permission(self.user)

    @override_settings(MARKETING_WHATSAPP_SEND_MODE='TEST')
    @patch.dict('os.environ', VALID_ENV, clear=False)
    def test_persisted_failed_message_has_no_access_token(self):
        campaign = setup_ready_test_campaign(self.user, recipient_count=1)

        def failing_send(*args, **kwargs):
            return {
                'ok': False,
                'status_code': 400,
                'error': json.dumps({
                    'error': {
                        'message': f'OAuthException with {FAKE_TOKEN}',
                        'code': 190,
                    },
                }),
            }

        execute_test_campaign_send(
            campaign.pk,
            created_by=self.user,
            send_callable=failing_send,
        )
        message = MarketingCampaignMessage.objects.filter(send_run__campaign=campaign).first()
        self.assertEqual(message.status, MESSAGE_STATUS_FAILED)
        self.assertNotIn(FAKE_TOKEN, message.error_message)
        self.assertIn('[REDACTED]', message.error_message)


class MarketingAdminWhatsAppErrorRedactionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('admin-sec', 'admin@test.com', 'pass')
        self.client = Client()
        self.client.force_login(self.user)
        self.admin_obj = MarketingCampaignMessageAdmin(MarketingCampaignMessage, AdminSite())
        self.template = make_test_send_template(self.user)
        self.campaign = make_campaign(
            make_audience(name='Security admin audience', created_by=self.user),
            self.user,
            name='Security admin campaign',
            channel=CHANNEL_WHATSAPP,
            status=STATUS_AUDIENCE_PREPARED,
            message_template=self.template,
        )
        self.recipient = MarketingCampaignRecipient.objects.create(
            campaign=self.campaign,
            phone_normalized='77009998877',
            display_name='Buyer',
            city='Алматы',
            roles=['Покупатель'],
            vehicle_summary='Toyota',
            is_test_contact=False,
            is_control_recipient=True,
            consent_status='granted',
            eligibility_status=ELIGIBILITY_ELIGIBLE,
        )
        send_run = MarketingCampaignSendRun.objects.create(
            campaign=self.campaign,
            template=self.template,
            mode=SEND_MODE_LIVE,
            status=SEND_RUN_STATUS_QUEUED,
            workflow_type=WORKFLOW_TYPE_SIMPLE_MAILING,
            recipient_scope=RECIPIENT_SCOPE_CONTROL_ONLY,
            total_count=1,
            queued_count=1,
            created_by=self.user,
            started_at=timezone.now(),
        )
        self.message = MarketingCampaignMessage.objects.create(
            send_run=send_run,
            campaign_recipient=self.recipient,
            phone_normalized=self.recipient.phone_normalized,
            template_name=self.template.meta_template_name,
            language_code=self.template.language_code,
            variables={},
            status=MESSAGE_STATUS_FAILED,
            error_code='190',
            error_message=f'Meta rejected request Bearer {FAKE_TOKEN}',
            wave_number=1,
            position_number=1,
            scheduled_at=timezone.now(),
        )

    @patch.dict('os.environ', VALID_ENV, clear=False)
    def test_admin_list_hides_historical_token(self):
        url = reverse('admin:marketing_marketingcampaignmessage_changelist')
        response = self.client.get(url, {'q': str(self.message.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, FAKE_TOKEN)
        self.assertContains(response, '[REDACTED]')

    @patch.dict('os.environ', VALID_ENV, clear=False)
    def test_admin_detail_hides_historical_token(self):
        safe_text = self.admin_obj.safe_error_message_display(self.message)
        self.assertNotIn(FAKE_TOKEN, safe_text)
        self.assertIn('[REDACTED]', safe_text)

        url = reverse('admin:marketing_marketingcampaignmessage_change', args=[self.message.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, FAKE_TOKEN)
        self.assertContains(response, '[REDACTED]')
