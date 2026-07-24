from __future__ import annotations

import uuid
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    BuyerPortalAccess,
    ContactConsent,
)
from marketing.models import MarketingCampaignMessage, MarketingCampaignSendRun
from marketing.services.audiences.constants import (
    GROUP_BUYERS,
    GROUP_TEST,
    SUBTYPE_PARTS_REQUESTS,
    SUBTYPE_TEST_CONTACTS,
)
from marketing.services.campaigns.constants import PURPOSE_PARTS_BUYERS, PURPOSE_TEST_CAMPAIGN
from marketing.services.campaigns.live_processor import (
    _finalize_send_run,
    cancel_live_send_run,
    mark_stuck_live_processing_as_delivery_unknown,
    process_marketing_live_send_batch,
)
from marketing.services.campaigns.live_send import create_live_send_queue
from marketing.services.campaigns.live_send_validation import (
    LiveSendValidationError,
    build_live_send_preflight,
)
from marketing.services.campaigns.preparation import prepare_campaign_snapshot
from marketing.services.campaigns.send_constants import (
    ERROR_CODE_DELIVERY_UNKNOWN,
    MESSAGE_STATUS_CANCELLED,
    MESSAGE_STATUS_FAILED,
    MESSAGE_STATUS_PROCESSING,
    MESSAGE_STATUS_QUEUED,
    MESSAGE_STATUS_SENT,
    MESSAGE_STATUS_SKIPPED,
    SEND_MODE_LIVE,
    SEND_RUN_STATUS_CANCELLED,
    SEND_RUN_STATUS_COMPLETED,
    SEND_RUN_STATUS_FAILED,
    SEND_RUN_STATUS_PARTIAL,
    SEND_RUN_STATUS_QUEUED,
    SEND_RUN_STATUS_RUNNING,
    VARIABLE_KEY_REQUEST_HISTORY_URL,
)
from marketing.services.campaigns.send_settings import get_marketing_whatsapp_send_mode
from marketing.services.templates.constants import META_STATUS_APPROVED
from marketing.tests.test_marketing_audiences import grant_consent, grant_marketing_permission, make_buyer
from marketing.tests.test_marketing_campaigns import make_audience, make_campaign, next_phone
from marketing.tests.test_marketing_campaign_send import ensure_portal_access, make_test_send_template


LIVE_SETTINGS = {
    'MARKETING_WHATSAPP_SEND_MODE': 'LIVE',
    'MARKETING_LIVE_BATCH_SIZE': 10,
    'MARKETING_LIVE_MAX_RECIPIENTS': 10,
    'MARKETING_LIVE_SEND_INTERVAL_SECONDS': 0,
}


def setup_ready_live_campaign(
    user: User,
    *,
    recipient_count: int = 1,
) -> 'MarketingCampaign':
    audience = make_audience(
        name=f'Live audience {next_phone()}',
        contact_group=GROUP_BUYERS,
        contact_subtype=SUBTYPE_PARTS_REQUESTS,
    )
    template = make_test_send_template(user)
    template.allow_test_campaign = False
    template.save(update_fields=['allow_test_campaign'])
    campaign = make_campaign(
        audience,
        user,
        purpose=PURPOSE_PARTS_BUYERS,
        name='Live campaign',
        message_template=template,
    )
    for _ in range(recipient_count):
        buyer = make_buyer(is_test_contact=False)
        grant_consent(buyer)
        ensure_portal_access(buyer)
    prepare_campaign_snapshot(campaign.pk)
    campaign.refresh_from_db()
    return campaign


def _mock_send_ok(phone, **kwargs):
    return {
        'ok': True,
        'status_code': 200,
        'message_id': f'wamid.live.{phone[-4:]}',
        'error': None,
    }


@override_settings(**LIVE_SETTINGS)
class MarketingLiveSendSettingsTests(TestCase):
    def test_live_mode_recognized(self):
        self.assertEqual(get_marketing_whatsapp_send_mode(), 'LIVE')


@override_settings(**LIVE_SETTINGS)
class MarketingLiveProcessorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)

    def test_off_mode_processor_noop(self):
        with self.settings(MARKETING_WHATSAPP_SEND_MODE='OFF'):
            result = process_marketing_live_send_batch()
        self.assertEqual(result.processed_count, 0)

    def test_test_mode_processor_noop(self):
        with self.settings(MARKETING_WHATSAPP_SEND_MODE='TEST'):
            result = process_marketing_live_send_batch()
        self.assertEqual(result.processed_count, 0)

    def test_live_queued_message_sent_outside_transaction(self):
        campaign = setup_ready_live_campaign(self.user)
        result = create_live_send_queue(
            campaign.pk,
            created_by=self.user,
            confirmation_text='LIVE',
        )
        reservation_done = {'value': False}
        from marketing.services.campaigns import live_processor

        real_reserve = live_processor._reserve_queued_messages

        def reserve_then_mark(limit):
            message_ids = real_reserve(limit)
            reservation_done['value'] = True
            return message_ids

        def send_after_reservation(*args, **kwargs):
            self.assertTrue(
                reservation_done['value'],
                'Meta send must happen after reservation transaction commits',
            )
            return _mock_send_ok(*args, **kwargs)

        with patch.object(live_processor, '_reserve_queued_messages', reserve_then_mark):
            batch = process_marketing_live_send_batch(send_callable=send_after_reservation)
        self.assertEqual(batch.sent_count, 1)
        message = MarketingCampaignMessage.objects.get(send_run_id=result.send_run_id)
        self.assertEqual(message.status, MESSAGE_STATUS_SENT)
        self.assertTrue(message.meta_message_id)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message', side_effect=_mock_send_ok)
    def test_batch_limit_respected(self, mocked):
        campaign = setup_ready_live_campaign(self.user, recipient_count=3)
        create_live_send_queue(campaign.pk, created_by=self.user, confirmation_text='LIVE')
        with self.settings(MARKETING_LIVE_BATCH_SIZE=2):
            result = process_marketing_live_send_batch()
        self.assertEqual(result.processed_count, 2)
        self.assertEqual(
            MarketingCampaignMessage.objects.filter(status=MESSAGE_STATUS_QUEUED).count(),
            1,
        )

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message', side_effect=_mock_send_ok)
    def test_two_processor_passes_do_not_double_send(self, mocked):
        campaign = setup_ready_live_campaign(self.user)
        create_live_send_queue(campaign.pk, created_by=self.user, confirmation_text='LIVE')
        process_marketing_live_send_batch()
        process_marketing_live_send_batch()
        mocked.assert_called_once()

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message', side_effect=_mock_send_ok)
    def test_revoked_after_queue_skipped_no_meta(self, mocked):
        campaign = setup_ready_live_campaign(self.user)
        buyer = campaign.recipients.filter(eligibility_status='eligible').first()
        create_live_send_queue(campaign.pk, created_by=self.user, confirmation_text='LIVE')
        ContactConsent.objects.filter(buyer__phone_normalized=buyer.phone_normalized).update(
            status=CONTACT_CONSENT_STATUS_REVOKED,
        )
        result = process_marketing_live_send_batch()
        mocked.assert_not_called()
        message = MarketingCampaignMessage.objects.get(campaign_recipient=buyer)
        self.assertEqual(message.status, MESSAGE_STATUS_SKIPPED)
        self.assertEqual(result.skipped_count, 1)

    def test_test_contact_never_queued(self):
        audience = make_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
        )
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
        preflight = build_live_send_preflight(campaign)
        self.assertFalse(preflight.allowed)

    def test_unknown_consent_not_live_eligible(self):
        campaign = setup_ready_live_campaign(self.user)
        buyer = campaign.recipients.first()
        buyer.consent_status = CONTACT_CONSENT_STATUS_UNKNOWN
        buyer.save(update_fields=['consent_status'])
        preflight = build_live_send_preflight(campaign)
        self.assertFalse(preflight.allowed)

    def test_consent_not_recorded_not_live_eligible(self):
        campaign = setup_ready_live_campaign(self.user)
        buyer = campaign.recipients.first()
        ContactConsent.objects.filter(buyer__phone_normalized=buyer.phone_normalized).delete()
        prepare_campaign_snapshot(campaign.pk)
        campaign.refresh_from_db()
        preflight = build_live_send_preflight(campaign)
        self.assertFalse(preflight.allowed)
        self.assertEqual(preflight.eligible_now_count, 0)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message', side_effect=_mock_send_ok)
    def test_inactive_template_blocks_at_processor(self, mocked):
        campaign = setup_ready_live_campaign(self.user)
        create_live_send_queue(campaign.pk, created_by=self.user, confirmation_text='LIVE')
        template = campaign.message_template
        template.is_active = False
        template.save(update_fields=['is_active'])
        process_marketing_live_send_batch()
        mocked.assert_not_called()
        message = MarketingCampaignMessage.objects.first()
        self.assertEqual(message.status, MESSAGE_STATUS_SKIPPED)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_timeout_not_auto_retried(self, mocked):
        mocked.side_effect = TimeoutError('network')
        campaign = setup_ready_live_campaign(self.user)
        create_live_send_queue(campaign.pk, created_by=self.user, confirmation_text='LIVE')
        process_marketing_live_send_batch()
        message = MarketingCampaignMessage.objects.first()
        self.assertEqual(message.error_code, ERROR_CODE_DELIVERY_UNKNOWN)
        mocked.assert_called_once()

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message', side_effect=_mock_send_ok)
    def test_cancel_run_skips_queued_messages(self, mocked):
        campaign = setup_ready_live_campaign(self.user)
        queue = create_live_send_queue(campaign.pk, created_by=self.user, confirmation_text='LIVE')
        cancel_live_send_run(queue.send_run_id)
        process_marketing_live_send_batch()
        mocked.assert_not_called()
        self.assertEqual(
            MarketingCampaignMessage.objects.filter(status=MESSAGE_STATUS_CANCELLED).count(),
            1,
        )


@override_settings(**LIVE_SETTINGS)
class MarketingLiveViewTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer', password='secret')
        self.campaign = setup_ready_live_campaign(self.user)

    def test_preflight_get_does_not_send(self):
        with patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message') as mocked:
            response = self.client.get(
                reverse('marketing:campaign_live_send_preflight', kwargs={'pk': self.campaign.pk}),
            )
        self.assertEqual(response.status_code, 200)
        mocked.assert_not_called()

    def test_execute_post_only_via_confirm(self):
        response = self.client.get(
            reverse('marketing:campaign_live_send_confirm', kwargs={'pk': self.campaign.pk}),
        )
        self.assertEqual(response.status_code, 200)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message', side_effect=_mock_send_ok)
    def test_confirm_post_creates_queue_no_meta(self, mocked):
        response = self.client.post(
            reverse('marketing:campaign_live_send_confirm', kwargs={'pk': self.campaign.pk}),
            {'confirmation_text': 'LIVE'},
        )
        self.assertEqual(response.status_code, 302)
        mocked.assert_not_called()
        self.assertEqual(MarketingCampaignSendRun.objects.filter(mode=SEND_MODE_LIVE).count(), 1)

    def test_off_mode_blocks_live(self):
        with self.settings(MARKETING_WHATSAPP_SEND_MODE='OFF'):
            preflight = build_live_send_preflight(self.campaign)
        self.assertFalse(preflight.allowed)

    def test_test_mode_blocks_live(self):
        with self.settings(MARKETING_WHATSAPP_SEND_MODE='TEST'):
            preflight = build_live_send_preflight(self.campaign)
        self.assertFalse(preflight.allowed)

    def test_test_campaign_live_blocked(self):
        audience = make_audience(
            name=f'Test live block {next_phone()}',
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
        )
        template = self.campaign.message_template
        template.allow_test_campaign = True
        template.save(update_fields=['allow_test_campaign'])
        test_campaign = make_campaign(
            audience,
            self.user,
            purpose=PURPOSE_TEST_CAMPAIGN,
            name='Test campaign live block',
            message_template=template,
        )
        buyer = make_buyer(is_test_contact=True)
        grant_consent(buyer)
        ensure_portal_access(buyer)
        prepare_campaign_snapshot(test_campaign.pk)
        preflight = build_live_send_preflight(test_campaign)
        self.assertFalse(preflight.allowed)

    def test_confirmation_mismatch_blocks(self):
        with self.assertRaises(LiveSendValidationError):
            create_live_send_queue(
                self.campaign.pk,
                created_by=self.user,
                confirmation_text='WRONG',
            )

    def test_repeat_launch_blocked(self):
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        preflight = build_live_send_preflight(self.campaign)
        self.assertFalse(preflight.allowed)

    def test_max_recipients_blocks_when_snapshot_too_large(self):
        for _ in range(2):
            buyer = make_buyer(is_test_contact=False)
            grant_consent(buyer)
            ensure_portal_access(buyer)
        prepare_campaign_snapshot(self.campaign.pk)
        self.campaign.refresh_from_db()
        with self.settings(MARKETING_LIVE_MAX_RECIPIENTS=2):
            preflight = build_live_send_preflight(self.campaign)
        self.assertFalse(preflight.allowed)
        self.assertTrue(any('лимит LIVE' in err for err in preflight.blocking_errors))

    def test_stale_snapshot_blocks_launch(self):
        self.campaign.audience.criteria = {'primary_cities': ['Алматы']}
        self.campaign.audience.save()
        preflight = build_live_send_preflight(self.campaign)
        self.assertFalse(preflight.allowed)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message', side_effect=_mock_send_ok)
    def test_successful_meta_response_saves_message_id(self, mocked):
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        process_marketing_live_send_batch()
        message = MarketingCampaignMessage.objects.first()
        self.assertTrue(message.meta_message_id.startswith('wamid.'))

    def test_no_eligible_recipients_blocks(self):
        ContactConsent.objects.all().delete()
        prepare_campaign_snapshot(self.campaign.pk)
        self.campaign.refresh_from_db()
        preflight = build_live_send_preflight(self.campaign)
        self.assertFalse(preflight.allowed)

    def test_inactive_template_blocks_at_launch(self):
        template = self.campaign.message_template
        template.is_active = False
        template.save(update_fields=['is_active'])
        preflight = build_live_send_preflight(self.campaign)
        self.assertFalse(preflight.allowed)
        self.assertTrue(any('неактивен' in err for err in preflight.blocking_errors))

    def test_unapproved_template_blocks_at_launch(self):
        template = self.campaign.message_template
        template.meta_status = 'draft'
        template.save(update_fields=['meta_status'])
        preflight = build_live_send_preflight(self.campaign)
        self.assertFalse(preflight.allowed)
        self.assertTrue(any('approved' in err for err in preflight.blocking_errors))

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message', side_effect=_mock_send_ok)
    def test_existing_sent_recipient_duplicate_blocked(self, mocked):
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        process_marketing_live_send_batch()
        preflight = build_live_send_preflight(self.campaign)
        self.assertFalse(preflight.allowed)

    def test_permission_required_for_preflight(self):
        self.client.logout()
        response = self.client.get(
            reverse('marketing:campaign_live_send_preflight', kwargs={'pk': self.campaign.pk}),
        )
        self.assertEqual(response.status_code, 302)

    def test_browser_supplied_recipient_data_ignored(self):
        recipient = self.campaign.recipients.first()
        response = self.client.post(
            reverse('marketing:campaign_live_send_confirm', kwargs={'pk': self.campaign.pk}),
            {
                'confirmation_text': 'LIVE',
                'phone_normalized': '77009999999',
                'recipient_id': recipient.pk,
                'variables': '{"request_history_url":"https://evil.example/"}',
            },
        )
        self.assertEqual(response.status_code, 302)
        message = MarketingCampaignMessage.objects.get(send_run__campaign=self.campaign)
        self.assertNotEqual(message.phone_normalized, '77009999999')
        self.assertNotIn('evil.example', str(message.variables))

    @patch('marketing.services.campaigns.live_processor.logger')
    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message', side_effect=_mock_send_ok)
    def test_logs_do_not_contain_full_phone_or_token(self, mocked, logger_mock):
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        process_marketing_live_send_batch()
        message = MarketingCampaignMessage.objects.first()
        full_phone = message.phone_normalized
        portal_token = str(message.variables.get('request_history_url', ''))
        for call in logger_mock.warning.call_args_list + logger_mock.exception.call_args_list:
            logged = ' '.join(str(part) for part in call.args)
            if full_phone:
                self.assertNotIn(full_phone, logged)
            if portal_token and len(portal_token) > 20:
                self.assertNotIn(portal_token, logged)


def _create_bare_live_run(campaign, *, status: str, user: User) -> MarketingCampaignSendRun:
    return MarketingCampaignSendRun.objects.create(
        campaign=campaign,
        template=campaign.message_template,
        mode=SEND_MODE_LIVE,
        status=status,
        total_count=1,
        queued_count=1 if status == SEND_RUN_STATUS_QUEUED else 0,
        created_by=user,
    )


@override_settings(**LIVE_SETTINGS)
class MarketingLiveConcurrencyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        self.campaign = setup_ready_live_campaign(self.user)

    def test_db_blocks_two_queued_live_runs(self):
        _create_bare_live_run(self.campaign, status=SEND_RUN_STATUS_QUEUED, user=self.user)
        with self.assertRaises(IntegrityError):
            _create_bare_live_run(self.campaign, status=SEND_RUN_STATUS_QUEUED, user=self.user)

    def test_db_blocks_two_running_live_runs(self):
        _create_bare_live_run(self.campaign, status=SEND_RUN_STATUS_RUNNING, user=self.user)
        with self.assertRaises(IntegrityError):
            _create_bare_live_run(self.campaign, status=SEND_RUN_STATUS_RUNNING, user=self.user)

    def test_db_blocks_running_plus_queued_live_runs(self):
        _create_bare_live_run(self.campaign, status=SEND_RUN_STATUS_RUNNING, user=self.user)
        with self.assertRaises(IntegrityError):
            _create_bare_live_run(self.campaign, status=SEND_RUN_STATUS_QUEUED, user=self.user)

    def test_db_blocks_queued_plus_running_live_runs(self):
        _create_bare_live_run(self.campaign, status=SEND_RUN_STATUS_QUEUED, user=self.user)
        with self.assertRaises(IntegrityError):
            _create_bare_live_run(self.campaign, status=SEND_RUN_STATUS_RUNNING, user=self.user)


@override_settings(**LIVE_SETTINGS)
class MarketingLiveSafetyTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer', password='secret')
        self.campaign = setup_ready_live_campaign(self.user)

    def test_duplicate_post_launch_blocked(self):
        response = self.client.post(
            reverse('marketing:campaign_live_send_confirm', kwargs={'pk': self.campaign.pk}),
            {'confirmation_text': 'LIVE'},
        )
        self.assertEqual(response.status_code, 302)
        run_count = MarketingCampaignSendRun.objects.filter(mode=SEND_MODE_LIVE).count()
        with self.assertRaises(LiveSendValidationError):
            create_live_send_queue(
                self.campaign.pk,
                created_by=self.user,
                confirmation_text='LIVE',
            )
        self.assertEqual(
            MarketingCampaignSendRun.objects.filter(mode=SEND_MODE_LIVE).count(),
            run_count,
        )

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message', side_effect=_mock_send_ok)
    def test_processing_message_not_resent(self, mocked):
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        message = MarketingCampaignMessage.objects.first()
        message.status = MESSAGE_STATUS_PROCESSING
        message.attempted_at = timezone.now()
        message.save(update_fields=['status', 'attempted_at'])
        process_marketing_live_send_batch()
        mocked.assert_not_called()

    def test_run_not_completed_while_processing_exists(self):
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        run = MarketingCampaignSendRun.objects.first()
        message = run.messages.first()
        message.status = MESSAGE_STATUS_PROCESSING
        message.attempted_at = timezone.now()
        message.save(update_fields=['status', 'attempted_at'])
        run.status = SEND_RUN_STATUS_RUNNING
        run.save(update_fields=['status'])
        _finalize_send_run(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, SEND_RUN_STATUS_RUNNING)
        self.assertIsNone(run.finished_at)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message', side_effect=_mock_send_ok)
    def test_audience_change_after_queue_does_not_expand_recipient_set(self, mocked):
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        message_count = MarketingCampaignMessage.objects.count()
        self.campaign.audience.criteria = {'primary_cities': ['Астана']}
        self.campaign.audience.save()
        buyer = make_buyer(is_test_contact=False)
        grant_consent(buyer)
        ensure_portal_access(buyer)
        self.campaign.refresh_from_db()
        self.assertTrue(self.campaign.is_snapshot_stale())
        self.assertEqual(MarketingCampaignMessage.objects.count(), message_count)
        process_marketing_live_send_batch()
        mocked.assert_called_once()

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message', side_effect=_mock_send_ok)
    def test_consent_revoked_after_queue_still_prevents_meta(self, mocked):
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        buyer = self.campaign.recipients.filter(eligibility_status='eligible').first()
        ContactConsent.objects.filter(buyer__phone_normalized=buyer.phone_normalized).update(
            status=CONTACT_CONSENT_STATUS_REVOKED,
        )
        process_marketing_live_send_batch()
        mocked.assert_not_called()
        message = MarketingCampaignMessage.objects.get(campaign_recipient=buyer)
        self.assertEqual(message.status, MESSAGE_STATUS_SKIPPED)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message', side_effect=_mock_send_ok)
    def test_cancelled_run_prevents_remaining_queued_meta_sends(self, mocked):
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        run = MarketingCampaignSendRun.objects.first()
        cancel_live_send_run(run.pk)
        process_marketing_live_send_batch()
        mocked.assert_not_called()
        self.assertEqual(
            MarketingCampaignMessage.objects.filter(status=MESSAGE_STATUS_CANCELLED).count(),
            1,
        )

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message', side_effect=_mock_send_ok)
    def test_audit_mark_delivery_unknown_finalizes_run(self, mocked):
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        run = MarketingCampaignSendRun.objects.first()
        message = run.messages.first()
        message.status = MESSAGE_STATUS_PROCESSING
        message.attempted_at = timezone.now()
        message.save(update_fields=['status', 'attempted_at'])
        run.status = SEND_RUN_STATUS_RUNNING
        run.save(update_fields=['status'])

        updated = mark_stuck_live_processing_as_delivery_unknown(message_ids=[message.pk])

        mocked.assert_not_called()
        self.assertEqual(updated, 1)
        message.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(message.status, MESSAGE_STATUS_FAILED)
        self.assertEqual(message.error_code, ERROR_CODE_DELIVERY_UNKNOWN)
        self.assertEqual(run.status, SEND_RUN_STATUS_FAILED)
        self.assertIsNotNone(run.finished_at)

    def test_audit_mark_does_not_finalize_run_with_remaining_queued(self):
        buyer = make_buyer(is_test_contact=False)
        grant_consent(buyer)
        ensure_portal_access(buyer)
        prepare_campaign_snapshot(self.campaign.pk)
        self.campaign.refresh_from_db()
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        run = MarketingCampaignSendRun.objects.first()
        messages = list(run.messages.order_by('id'))
        self.assertEqual(len(messages), 2)
        messages[0].status = MESSAGE_STATUS_PROCESSING
        messages[0].attempted_at = timezone.now()
        messages[0].save(update_fields=['status', 'attempted_at'])
        run.status = SEND_RUN_STATUS_RUNNING
        run.save(update_fields=['status'])

        mark_stuck_live_processing_as_delivery_unknown(message_ids=[messages[0].pk])

        run.refresh_from_db()
        self.assertEqual(run.status, SEND_RUN_STATUS_RUNNING)
        self.assertIsNone(run.finished_at)
        self.assertEqual(
            run.messages.filter(status=MESSAGE_STATUS_QUEUED).count(),
            1,
        )


def _finalize_run_with_message_statuses(run, statuses: list[str]) -> None:
    messages = list(run.messages.order_by('id'))
    for message, status in zip(messages, statuses, strict=True):
        message.status = status
        message.save(update_fields=['status'])
    run.status = SEND_RUN_STATUS_RUNNING
    run.save(update_fields=['status'])
    _finalize_send_run(run.pk)
    run.refresh_from_db()


@override_settings(**LIVE_SETTINGS)
class MarketingLiveFinalizationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        self.campaign = setup_ready_live_campaign(self.user)

    def test_sent_zero_failed_one_run_failed(self):
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        run = MarketingCampaignSendRun.objects.first()
        _finalize_run_with_message_statuses(run, [MESSAGE_STATUS_FAILED])
        self.assertEqual(run.status, SEND_RUN_STATUS_FAILED)

    def test_sent_zero_skipped_only_run_failed(self):
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        run = MarketingCampaignSendRun.objects.first()
        _finalize_run_with_message_statuses(run, [MESSAGE_STATUS_SKIPPED])
        self.assertEqual(run.status, SEND_RUN_STATUS_FAILED)

    def test_sent_zero_failed_and_skipped_run_failed(self):
        buyer = make_buyer(is_test_contact=False)
        grant_consent(buyer)
        ensure_portal_access(buyer)
        prepare_campaign_snapshot(self.campaign.pk)
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        run = MarketingCampaignSendRun.objects.first()
        _finalize_run_with_message_statuses(
            run,
            [MESSAGE_STATUS_FAILED, MESSAGE_STATUS_SKIPPED],
        )
        self.assertEqual(run.status, SEND_RUN_STATUS_FAILED)

    def test_sent_positive_with_failed_run_partial(self):
        buyer = make_buyer(is_test_contact=False)
        grant_consent(buyer)
        ensure_portal_access(buyer)
        prepare_campaign_snapshot(self.campaign.pk)
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        run = MarketingCampaignSendRun.objects.first()
        _finalize_run_with_message_statuses(
            run,
            [MESSAGE_STATUS_SENT, MESSAGE_STATUS_FAILED],
        )
        self.assertEqual(run.status, SEND_RUN_STATUS_PARTIAL)

    def test_sent_positive_with_skipped_run_partial(self):
        buyer = make_buyer(is_test_contact=False)
        grant_consent(buyer)
        ensure_portal_access(buyer)
        prepare_campaign_snapshot(self.campaign.pk)
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        run = MarketingCampaignSendRun.objects.first()
        _finalize_run_with_message_statuses(
            run,
            [MESSAGE_STATUS_SENT, MESSAGE_STATUS_SKIPPED],
        )
        self.assertEqual(run.status, SEND_RUN_STATUS_PARTIAL)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message', side_effect=_mock_send_ok)
    def test_all_sent_run_completed(self, mocked):
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        process_marketing_live_send_batch()
        run = MarketingCampaignSendRun.objects.first()
        self.assertEqual(run.status, SEND_RUN_STATUS_COMPLETED)
        self.assertEqual(run.sent_count, 1)
        self.assertEqual(run.failed_count, 0)
        self.assertEqual(run.skipped_count, 0)

    def test_queued_message_keeps_run_non_terminal(self):
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        run = MarketingCampaignSendRun.objects.first()
        run.status = SEND_RUN_STATUS_RUNNING
        run.save(update_fields=['status'])
        _finalize_send_run(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, SEND_RUN_STATUS_RUNNING)
        self.assertIsNone(run.finished_at)

    def test_cancelled_run_remains_cancelled_on_finalize(self):
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        run = MarketingCampaignSendRun.objects.first()
        cancel_live_send_run(run.pk)
        _finalize_send_run(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, SEND_RUN_STATUS_CANCELLED)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message', side_effect=_mock_send_ok)
    def test_delivery_unknown_after_timeout_run_failed(self, mocked):
        mocked.side_effect = TimeoutError('network')
        create_live_send_queue(self.campaign.pk, created_by=self.user, confirmation_text='LIVE')
        process_marketing_live_send_batch()
        run = MarketingCampaignSendRun.objects.first()
        self.assertEqual(run.status, SEND_RUN_STATUS_FAILED)
        self.assertEqual(run.sent_count, 0)
        self.assertEqual(run.failed_count, 1)
