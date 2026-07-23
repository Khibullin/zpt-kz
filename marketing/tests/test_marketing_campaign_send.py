from __future__ import annotations

import uuid
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import (
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    BuyerBroadcastCampaign,
    BuyerBroadcastRecipient,
    BuyerPortalAccess,
)
from core.whatsapp_template_sender import send_whatsapp_template_message
from marketing.models import (
    MarketingCampaign,
    MarketingCampaignMessage,
    MarketingCampaignSendRun,
)
from marketing.services.audiences.constants import GROUP_TEST, SUBTYPE_TEST_CONTACTS
from marketing.services.campaigns.constants import PURPOSE_PARTS_BUYERS, PURPOSE_TEST_CAMPAIGN
from marketing.services.campaigns.preparation import prepare_campaign_snapshot
from marketing.services.campaigns.send_constants import (
    FORBIDDEN_SAMPLE_ACCESS_TOKEN,
    MESSAGE_STATUS_SENT,
    SEND_MODE_TEST,
    SEND_RUN_STATUS_COMPLETED,
    VARIABLE_KEY_REQUEST_HISTORY_URL,
)
from marketing.services.campaigns.send_settings import get_marketing_whatsapp_send_mode
from marketing.services.campaigns.send_variables import resolve_request_history_url
from marketing.services.campaigns.test_send import execute_test_campaign_send
from marketing.services.templates.constants import META_STATUS_APPROVED, META_STATUS_DRAFT
from marketing.tests.test_marketing_audiences import grant_consent, grant_marketing_permission, make_buyer
from marketing.tests.test_marketing_campaigns import make_audience, make_campaign
from marketing.tests.test_marketing_templates import make_template


def ensure_portal_access(buyer, *, access_token=None) -> BuyerPortalAccess:
    token = access_token or uuid.uuid4()
    portal, _ = BuyerPortalAccess.objects.update_or_create(
        phone_normalized=buyer.phone_normalized,
        defaults={'access_token': token},
    )
    return portal


def make_test_send_template(user: User):
    return make_template(
        user,
        name='Buyer platform info',
        meta_template_name='zpt_buyer_platform_info',
        language_code='ru',
        allow_test_campaign=True,
        allowed_purposes=[PURPOSE_PARTS_BUYERS],
        variables=[{
            'key': VARIABLE_KEY_REQUEST_HISTORY_URL,
            'label': 'История заявок',
            'required': True,
            'example': 'https://zpt.kz/my-requests/example/',
        }],
    )


def setup_ready_test_campaign(
    user: User,
    *,
    recipient_count: int = 2,
) -> MarketingCampaign:
    audience = make_audience(
        name='Test audience send',
        contact_group=GROUP_TEST,
        contact_subtype=SUBTYPE_TEST_CONTACTS,
    )
    template = make_test_send_template(user)
    campaign = make_campaign(
        audience,
        user,
        purpose=PURPOSE_TEST_CAMPAIGN,
        name='Test send campaign',
        message_template=template,
    )
    for _ in range(recipient_count):
        buyer = make_buyer(is_test_contact=True)
        grant_consent(buyer)
        ensure_portal_access(buyer)
    prepare_campaign_snapshot(campaign.pk)
    campaign.refresh_from_db()
    return campaign


class MarketingSendSettingsTests(TestCase):
    def test_default_send_mode_is_off(self):
        with self.settings(MARKETING_WHATSAPP_SEND_MODE='OFF'):
            self.assertEqual(get_marketing_whatsapp_send_mode(), 'OFF')


@override_settings(MARKETING_WHATSAPP_SEND_MODE='TEST')
class MarketingCampaignTestSendTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer', password='secret')

    def _post_execute(self, campaign: MarketingCampaign):
        self.client.get(
            reverse('marketing:campaign_test_send_preflight', kwargs={'pk': campaign.pk}),
        )
        return self.client.post(
            reverse('marketing:campaign_test_send_execute', kwargs={'pk': campaign.pk}),
        )

    def _mock_send_ok(self, phone, **kwargs):
        return {
            'ok': True,
            'status_code': 200,
            'message_id': f'wamid.test.{phone}',
            'error': None,
        }

    def _mock_send_fail(self, phone, **kwargs):
        return {
            'ok': False,
            'status_code': 400,
            'message_id': '',
            'error': {'error': {'code': 131047, 'message': 'Re-engagement message'}},
        }

    @override_settings(MARKETING_WHATSAPP_SEND_MODE='OFF')
    def test_off_mode_blocks_network_send(self):
        campaign = setup_ready_test_campaign(self.user)
        with patch('marketing.services.campaigns.test_send.send_whatsapp_template_message') as mocked:
            from marketing.services.campaigns.send_validation import TestSendValidationError

            with self.assertRaises(TestSendValidationError):
                execute_test_campaign_send(campaign.pk, created_by=self.user)
            mocked.assert_not_called()

    def test_test_mode_allows_only_test_campaign(self):
        audience = make_audience()
        template = make_test_send_template(self.user)
        campaign = make_campaign(
            audience,
            self.user,
            purpose=PURPOSE_PARTS_BUYERS,
            message_template=template,
        )
        buyer = make_buyer(is_test_contact=True)
        grant_consent(buyer)
        ensure_portal_access(buyer)
        prepare_campaign_snapshot(campaign.pk)
        from marketing.services.campaigns.send_validation import build_test_send_preflight

        preflight = build_test_send_preflight(campaign)
        self.assertFalse(preflight.allowed)
        self.assertTrue(any('test_campaign' in item for item in preflight.blocking_errors))

    def test_test_mode_blocks_non_test_recipient_in_eligible_set(self):
        audience = make_audience(
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
        )
        template = make_test_send_template(self.user)
        campaign = make_campaign(
            audience,
            self.user,
            purpose=PURPOSE_TEST_CAMPAIGN,
            message_template=template,
        )
        buyer = make_buyer(is_test_contact=False)
        grant_consent(buyer)
        ensure_portal_access(buyer)
        prepare_campaign_snapshot(campaign.pk)
        from marketing.services.campaigns.send_validation import build_test_send_preflight

        preflight = build_test_send_preflight(campaign)
        self.assertFalse(preflight.allowed)

    def test_test_mode_blocks_more_than_two_recipients(self):
        campaign = setup_ready_test_campaign(self.user, recipient_count=3)
        from marketing.services.campaigns.send_validation import build_test_send_preflight

        preflight = build_test_send_preflight(campaign)
        self.assertFalse(preflight.allowed)
        self.assertTrue(any('не более 2' in item for item in preflight.blocking_errors))

    def test_revoked_consent_blocks_recipient(self):
        audience = make_audience(
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
        )
        template = make_test_send_template(self.user)
        campaign = make_campaign(
            audience,
            self.user,
            purpose=PURPOSE_TEST_CAMPAIGN,
            message_template=template,
        )
        buyer = make_buyer(is_test_contact=True)
        grant_consent(buyer, status=CONTACT_CONSENT_STATUS_REVOKED)
        ensure_portal_access(buyer)
        prepare_campaign_snapshot(campaign.pk)
        from marketing.services.campaigns.send_validation import build_test_send_preflight

        preflight = build_test_send_preflight(campaign)
        self.assertFalse(preflight.allowed)

    def test_unknown_consent_blocks_recipient(self):
        audience = make_audience(
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
        )
        template = make_test_send_template(self.user)
        campaign = make_campaign(
            audience,
            self.user,
            purpose=PURPOSE_TEST_CAMPAIGN,
            message_template=template,
        )
        buyer = make_buyer(is_test_contact=True)
        grant_consent(buyer, status=CONTACT_CONSENT_STATUS_UNKNOWN)
        ensure_portal_access(buyer)
        prepare_campaign_snapshot(campaign.pk)
        from marketing.services.campaigns.send_validation import build_test_send_preflight

        preflight = build_test_send_preflight(campaign)
        self.assertFalse(preflight.allowed)

    def test_not_recorded_consent_blocks_recipient(self):
        audience = make_audience(
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
        )
        template = make_test_send_template(self.user)
        campaign = make_campaign(
            audience,
            self.user,
            purpose=PURPOSE_TEST_CAMPAIGN,
            message_template=template,
        )
        buyer = make_buyer(is_test_contact=True)
        ensure_portal_access(buyer)
        prepare_campaign_snapshot(campaign.pk)
        from marketing.services.campaigns.send_validation import build_test_send_preflight

        preflight = build_test_send_preflight(campaign)
        self.assertFalse(preflight.allowed)

    def test_stale_snapshot_blocks_send(self):
        campaign = setup_ready_test_campaign(self.user)
        campaign.audience.is_active = False
        campaign.audience.save(update_fields=['is_active'])
        from marketing.services.campaigns.send_validation import build_test_send_preflight

        preflight = build_test_send_preflight(campaign)
        self.assertFalse(preflight.allowed)

    def test_template_not_approved_blocks(self):
        campaign = setup_ready_test_campaign(self.user)
        template = campaign.message_template
        template.meta_status = META_STATUS_DRAFT
        template.save(update_fields=['meta_status'])
        from marketing.services.campaigns.send_validation import build_test_send_preflight

        preflight = build_test_send_preflight(campaign)
        self.assertFalse(preflight.allowed)

    def test_inactive_template_blocks(self):
        campaign = setup_ready_test_campaign(self.user)
        template = campaign.message_template
        template.is_active = False
        template.save(update_fields=['is_active'])
        from marketing.services.campaigns.send_validation import build_test_send_preflight

        preflight = build_test_send_preflight(campaign)
        self.assertFalse(preflight.allowed)

    def test_incompatible_template_blocks(self):
        template = make_test_send_template(self.user)
        audience = make_audience(
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
        )
        campaign = make_campaign(
            audience,
            self.user,
            purpose=PURPOSE_TEST_CAMPAIGN,
            message_template=template,
        )
        buyer = make_buyer(is_test_contact=True)
        grant_consent(buyer)
        ensure_portal_access(buyer)
        prepare_campaign_snapshot(campaign.pk)
        template.allow_test_campaign = False
        template.save(update_fields=['allow_test_campaign'])
        from marketing.services.campaigns.send_validation import build_test_send_preflight

        preflight = build_test_send_preflight(campaign)
        self.assertFalse(preflight.allowed)

    def test_missing_request_history_url_blocks(self):
        audience = make_audience(
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
        )
        template = make_test_send_template(self.user)
        campaign = make_campaign(
            audience,
            self.user,
            purpose=PURPOSE_TEST_CAMPAIGN,
            message_template=template,
        )
        buyer = make_buyer(is_test_contact=True)
        grant_consent(buyer)
        prepare_campaign_snapshot(campaign.pk)
        from marketing.services.campaigns.send_validation import build_test_send_preflight

        preflight = build_test_send_preflight(campaign)
        self.assertFalse(preflight.allowed)

    def test_preflight_get_never_calls_meta(self):
        campaign = setup_ready_test_campaign(self.user)
        with patch('marketing.services.campaigns.test_send.send_whatsapp_template_message') as mocked:
            response = self.client.get(
                reverse('marketing:campaign_test_send_preflight', kwargs={'pk': campaign.pk}),
            )
            self.assertEqual(response.status_code, 200)
            mocked.assert_not_called()

    def test_get_execute_never_sends(self):
        campaign = setup_ready_test_campaign(self.user)
        with patch('marketing.services.campaigns.test_send.send_whatsapp_template_message') as mocked:
            response = self.client.get(
                reverse('marketing:campaign_test_send_execute', kwargs={'pk': campaign.pk}),
            )
            self.assertEqual(response.status_code, 405)
            mocked.assert_not_called()

    @patch('marketing.services.campaigns.test_send.send_whatsapp_template_message')
    def test_post_successfully_sends_two_test_recipients(self, mocked):
        mocked.side_effect = self._mock_send_ok
        campaign = setup_ready_test_campaign(self.user, recipient_count=2)
        response = self._post_execute(campaign)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mocked.call_count, 2)
        run = MarketingCampaignSendRun.objects.get(campaign=campaign)
        self.assertEqual(run.sent_count, 2)
        self.assertEqual(run.mode, SEND_MODE_TEST)

    @patch('marketing.services.campaigns.test_send.send_whatsapp_template_message')
    def test_each_recipient_gets_own_request_history_url(self, mocked):
        captured_urls = []

        def capture_send(phone, **kwargs):
            params = kwargs.get('body_parameters') or []
            if params:
                captured_urls.append(params[0]['text'])
            return self._mock_send_ok(phone, **kwargs)

        mocked.side_effect = capture_send
        campaign = setup_ready_test_campaign(self.user, recipient_count=2)
        recipients = list(campaign.recipients.filter(is_test_contact=True))
        expected_urls = []
        for recipient in recipients:
            portal = BuyerPortalAccess.objects.get(phone_normalized=recipient.phone_normalized)
            expected_urls.append(f'https://zpt.kz/my-requests/{portal.access_token}/')

        self._post_execute(campaign)
        self.assertEqual(len(captured_urls), 2)
        self.assertCountEqual(captured_urls, expected_urls)

    def test_sample_uuid_never_used_in_actual_send(self):
        audience = make_audience(
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
        )
        template = make_test_send_template(self.user)
        campaign = make_campaign(
            audience,
            self.user,
            purpose=PURPOSE_TEST_CAMPAIGN,
            message_template=template,
        )
        buyer = make_buyer(is_test_contact=True)
        grant_consent(buyer)
        ensure_portal_access(
            buyer,
            access_token=uuid.UUID(FORBIDDEN_SAMPLE_ACCESS_TOKEN),
        )
        prepare_campaign_snapshot(campaign.pk)
        from marketing.services.campaigns.send_validation import build_test_send_preflight

        preflight = build_test_send_preflight(campaign)
        self.assertFalse(preflight.allowed)

    @patch('marketing.services.campaigns.test_send.send_whatsapp_template_message')
    def test_meta_message_id_saved(self, mocked):
        mocked.side_effect = self._mock_send_ok
        campaign = setup_ready_test_campaign(self.user, recipient_count=1)
        self._post_execute(campaign)
        message = MarketingCampaignMessage.objects.filter(status=MESSAGE_STATUS_SENT).first()
        self.assertIsNotNone(message)
        self.assertTrue(message.meta_message_id.startswith('wamid.test.'))

    @patch('marketing.services.campaigns.test_send.send_whatsapp_template_message')
    def test_meta_error_saved(self, mocked):
        mocked.side_effect = self._mock_send_fail
        campaign = setup_ready_test_campaign(self.user, recipient_count=1)
        self._post_execute(campaign)
        message = MarketingCampaignMessage.objects.filter(status='failed').first()
        self.assertIsNotNone(message)
        self.assertIn('131047', message.error_code)
        self.assertIn('Re-engagement', message.error_message)

    @patch('marketing.services.campaigns.test_send.send_whatsapp_template_message')
    def test_partial_success_counted(self, mocked):
        calls = {'count': 0}

        def alternating(phone, **kwargs):
            calls['count'] += 1
            if calls['count'] == 1:
                return self._mock_send_ok(phone, **kwargs)
            return self._mock_send_fail(phone, **kwargs)

        mocked.side_effect = alternating
        campaign = setup_ready_test_campaign(self.user, recipient_count=2)
        self._post_execute(campaign)
        run = MarketingCampaignSendRun.objects.get(campaign=campaign)
        self.assertEqual(run.sent_count, 1)
        self.assertEqual(run.failed_count, 1)
        self.assertEqual(run.status, 'completed_with_errors')

    @patch('marketing.services.campaigns.test_send.send_whatsapp_template_message')
    def test_partial_success_repeat_post_does_not_resend_sent_recipient(self, mocked):
        calls = {'count': 0}

        def alternating(phone, **kwargs):
            calls['count'] += 1
            if calls['count'] == 1:
                return self._mock_send_ok(phone, **kwargs)
            return self._mock_send_fail(phone, **kwargs)

        mocked.side_effect = alternating
        campaign = setup_ready_test_campaign(self.user, recipient_count=2)
        self._post_execute(campaign)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(
            MarketingCampaignMessage.objects.filter(status=MESSAGE_STATUS_SENT).count(),
            1,
        )
        response = self._post_execute(campaign)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(MarketingCampaignSendRun.objects.filter(campaign=campaign).count(), 1)

    @patch('marketing.services.campaigns.test_send.send_whatsapp_template_message')
    def test_double_post_does_not_resend_successful_messages(self, mocked):
        mocked.side_effect = self._mock_send_ok
        campaign = setup_ready_test_campaign(self.user, recipient_count=2)
        url = reverse('marketing:campaign_test_send_execute', kwargs={'pk': campaign.pk})
        self._post_execute(campaign)
        self.assertEqual(mocked.call_count, 2)
        response = self._post_execute(campaign)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(MarketingCampaignSendRun.objects.filter(campaign=campaign).count(), 1)

    def test_legacy_buyer_broadcast_unchanged(self):
        before_campaigns = BuyerBroadcastCampaign.objects.count()
        before_recipients = BuyerBroadcastRecipient.objects.count()
        campaign = setup_ready_test_campaign(self.user, recipient_count=1)
        with patch('marketing.services.campaigns.test_send.send_whatsapp_template_message') as mocked:
            mocked.side_effect = self._mock_send_ok
            self._post_execute(campaign)
        self.assertEqual(BuyerBroadcastCampaign.objects.count(), before_campaigns)
        self.assertEqual(BuyerBroadcastRecipient.objects.count(), before_recipients)

    @patch.dict('os.environ', {
        'WHATSAPP_PHONE_NUMBER_ID': '',
        'WHATSAPP_ACCESS_TOKEN': '',
    })
    def test_service_whatsapp_sender_still_works(self):
        result = send_whatsapp_template_message(
            '77001234567',
            template_name='service_template',
            template_language='ru',
            body_parameters=[{'type': 'text', 'text': 'hello'}],
        )
        self.assertFalse(result['ok'])
        self.assertIn('ENV', result['error'])

    def test_marketing_permission_required(self):
        outsider = User.objects.create_user('outsider', password='secret', is_staff=True)
        client = Client()
        client.login(username='outsider', password='secret')
        campaign = setup_ready_test_campaign(self.user)
        response = client.get(reverse('marketing:campaign_test_send_preflight', kwargs={'pk': campaign.pk}))
        self.assertEqual(response.status_code, 403)

    def test_csrf_required_for_execute(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(username='marketer', password='secret')
        campaign = setup_ready_test_campaign(self.user, recipient_count=1)
        response = csrf_client.post(
            reverse('marketing:campaign_test_send_execute', kwargs={'pk': campaign.pk}),
        )
        self.assertEqual(response.status_code, 403)

    @patch('marketing.services.campaigns.test_send.send_whatsapp_template_message')
    def test_history_page_shows_send_run(self, mocked):
        mocked.side_effect = self._mock_send_ok
        campaign = setup_ready_test_campaign(self.user, recipient_count=1)
        self._post_execute(campaign)
        response = self.client.get(reverse('marketing:history'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'zpt_buyer_platform_info')
        self.assertContains(response, campaign.name)

    @patch('marketing.services.campaigns.test_send.send_whatsapp_template_message')
    def test_uses_template_meta_name_not_hardcoded(self, mocked):
        mocked.side_effect = self._mock_send_ok
        campaign = setup_ready_test_campaign(self.user, recipient_count=1)
        self._post_execute(campaign)
        _, kwargs = mocked.call_args
        self.assertEqual(kwargs['template_name'], 'zpt_buyer_platform_info')
        self.assertEqual(kwargs['template_language'], 'ru')

    @patch('marketing.services.campaigns.test_send.send_whatsapp_template_message')
    def test_meta_payload_uses_body_parameters_not_literal_placeholders(self, mocked):
        mocked.side_effect = self._mock_send_ok
        campaign = setup_ready_test_campaign(self.user, recipient_count=1)
        recipient = campaign.recipients.filter(is_test_contact=True).first()
        portal = BuyerPortalAccess.objects.get(phone_normalized=recipient.phone_normalized)
        expected_url = f'https://zpt.kz/my-requests/{portal.access_token}/'

        self._post_execute(campaign)

        mocked.assert_called_once()
        _, kwargs = mocked.call_args
        self.assertEqual(kwargs['template_name'], 'zpt_buyer_platform_info')
        self.assertEqual(kwargs['template_language'], 'ru')
        body_parameters = kwargs['body_parameters']
        self.assertEqual(len(body_parameters), 1)
        self.assertEqual(body_parameters[0]['type'], 'text')
        self.assertEqual(body_parameters[0]['text'], expected_url)
        self.assertNotIn('{{1}}', body_parameters[0]['text'])
        self.assertNotIn(FORBIDDEN_SAMPLE_ACCESS_TOKEN, body_parameters[0]['text'])

    def test_ambiguous_buyer_portal_access_blocks_preflight(self):
        audience = make_audience(
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
        )
        template = make_test_send_template(self.user)
        campaign = make_campaign(
            audience,
            self.user,
            purpose=PURPOSE_TEST_CAMPAIGN,
            message_template=template,
        )
        buyer = make_buyer(is_test_contact=True)
        grant_consent(buyer)
        ensure_portal_access(buyer)
        prepare_campaign_snapshot(campaign.pk)
        campaign.refresh_from_db()

        from marketing.services.campaigns.send_variables import VariableResolutionError

        with patch(
            'marketing.services.campaigns.send_validation.resolve_template_variables_for_recipient',
            side_effect=VariableResolutionError(
                'Неоднозначный BuyerPortalAccess для номера телефона.',
                recipient_phone=buyer.phone_normalized,
            ),
        ):
            from marketing.services.campaigns.send_validation import build_test_send_preflight

            preflight = build_test_send_preflight(campaign)
        self.assertFalse(preflight.allowed)
        self.assertTrue(
            any('Неоднозначный BuyerPortalAccess' in item for item in preflight.blocking_errors),
            preflight.blocking_errors,
        )

    def test_resolve_request_history_url_rejects_ambiguous_portal_access(self):
        from unittest.mock import MagicMock

        from marketing.services.campaigns.send_variables import (
            VariableResolutionError,
            resolve_request_history_url,
        )

        mocked_qs = MagicMock()
        mocked_qs.count.return_value = 2
        with patch('marketing.services.campaigns.send_variables.BuyerPortalAccess.objects.filter', return_value=mocked_qs):
            with self.assertRaises(VariableResolutionError) as ctx:
                resolve_request_history_url('77011910000')
        self.assertIn('Неоднозначный BuyerPortalAccess', str(ctx.exception))

    def test_production_test_contact_phones_resolve_request_history_url(self):
        for phone in ('77011910000', '77713607040'):
            with self.subTest(phone=phone):
                buyer = make_buyer(phone_normalized=phone, is_test_contact=True)
                grant_consent(buyer)
                portal = ensure_portal_access(buyer)
                url = resolve_request_history_url(phone)
                self.assertIsNotNone(url)
                self.assertIn('/my-requests/', url)
                self.assertIn(str(portal.access_token), url)
                self.assertNotIn(FORBIDDEN_SAMPLE_ACCESS_TOKEN, url)

    @patch('marketing.services.campaigns.test_send.send_whatsapp_template_message')
    def test_all_failed_repeat_post_blocked_without_meta_resend(self, mocked):
        mocked.side_effect = self._mock_send_fail
        campaign = setup_ready_test_campaign(self.user, recipient_count=2)
        self._post_execute(campaign)
        self.assertEqual(mocked.call_count, 2)
        response = self._post_execute(campaign)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(MarketingCampaignSendRun.objects.filter(campaign=campaign).count(), 1)


class MarketingCampaignSendRunProtectTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)

    def test_campaign_delete_blocked_when_send_run_exists(self):
        campaign = setup_ready_test_campaign(self.user, recipient_count=1)
        template = campaign.message_template
        recipient = campaign.recipients.first()
        send_run = MarketingCampaignSendRun.objects.create(
            campaign=campaign,
            template=template,
            mode=SEND_MODE_TEST,
            status=SEND_RUN_STATUS_COMPLETED,
            total_count=1,
            sent_count=1,
            created_by=self.user,
        )
        message = MarketingCampaignMessage.objects.create(
            send_run=send_run,
            campaign_recipient=recipient,
            phone_normalized=recipient.phone_normalized,
            template_name=template.meta_template_name,
            language_code=template.language_code,
            variables={},
            status=MESSAGE_STATUS_SENT,
        )
        campaign_id = campaign.pk
        send_run_id = send_run.pk
        message_id = message.pk

        with self.assertRaises(ProtectedError):
            campaign.delete()

        self.assertTrue(MarketingCampaign.objects.filter(pk=campaign_id).exists())
        self.assertTrue(MarketingCampaignSendRun.objects.filter(pk=send_run_id).exists())
        self.assertTrue(MarketingCampaignMessage.objects.filter(pk=message_id).exists())

    def test_campaign_without_send_run_can_be_deleted(self):
        audience = make_audience()
        campaign = make_campaign(
            audience,
            self.user,
            name='Draft without send history',
        )
        campaign_id = campaign.pk
        campaign.delete()
        self.assertFalse(MarketingCampaign.objects.filter(pk=campaign_id).exists())
