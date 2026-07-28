from __future__ import annotations

import uuid
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from core.models import (
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    BuyerContact,
    ContactConsent,
)
from marketing.models import MarketingCampaign, MarketingCampaignMessage, MarketingCampaignSendRun
from marketing.services.campaigns.live_processor import (
    _finalize_send_run,
    cancel_live_send_run,
    mark_stuck_live_processing_as_delivery_unknown,
    process_marketing_live_send_batch,
)
from marketing.services.campaigns.live_simple_waves import simple_mailing_has_active_run
from marketing.services.campaigns.send_constants import (
    ACTIVE_SIMPLE_MAILING_LOCK_VALUE,
    MESSAGE_STATUS_FAILED,
    MESSAGE_STATUS_PROCESSING,
    MESSAGE_STATUS_QUEUED,
    MESSAGE_STATUS_SENT,
    MESSAGE_STATUS_SKIPPED,
    SEND_RUN_STATUS_CANCELLED,
    SEND_RUN_STATUS_COMPLETED,
    SEND_RUN_STATUS_FAILED,
    SEND_RUN_STATUS_PARTIAL,
    SEND_RUN_STATUS_RUNNING,
    WORKFLOW_TYPE_SIMPLE_MAILING,
)
from marketing.services.simple_mailing.constants import (
    RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
    RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
)
from marketing.services.simple_mailing.launch import SimpleMailingLaunchError, launch_simple_mailing
from marketing.tests.test_marketing_audiences import grant_consent, make_buyer
from marketing.tests.test_marketing_campaign_live_send import LIVE_SETTINGS
from marketing.tests.test_marketing_campaign_send import ensure_portal_access, make_test_send_template
from marketing.tests.test_marketing_simple_mailing import make_request


LIVE_SIMPLE_SETTINGS = {
    **LIVE_SETTINGS,
    'MARKETING_SIMPLE_WAVE_SIZE': 10,
    'MARKETING_SIMPLE_WAVE_INTERVAL_MINUTES': 5,
    'MARKETING_LIVE_SEND_INTERVAL_SECONDS': 0,
}


def _launch_draft(*, count: int, template_id: int) -> dict:
    return {
        'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
        'recipient_scope': RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
        'all_brands': True,
        'brands': [],
        'count': count,
        'template_id': template_id,
    }


def _make_n_request_buyers(count: int) -> User:
    user = User.objects.create_user(f'lock-{count}-{uuid.uuid4().hex[:6]}', password='secret')
    for _ in range(count):
        buyer = make_buyer(is_test_contact=False)
        make_request(buyer, brand='Toyota')
        grant_consent(buyer, CONTACT_CONSENT_STATUS_GRANTED)
        ensure_portal_access(buyer)
    return user


def _launch_simple(*, user: User, count: int) -> MarketingCampaignSendRun:
    template = make_test_send_template(user)
    result = launch_simple_mailing(
        draft=_launch_draft(count=count, template_id=template.pk),
        template=template,
        created_by=user,
        launch_key=str(uuid.uuid4()),
    )
    send_run = MarketingCampaignSendRun.objects.get(pk=result.send_run_id)
    assert send_run.active_simple_mailing_lock == ACTIVE_SIMPLE_MAILING_LOCK_VALUE
    return send_run


def _mock_send_ok(phone, **kwargs):
    return {
        'ok': True,
        'status_code': 200,
        'message_id': f'wamid.lock.{phone[-4:]}',
        'error': None,
    }


def _mock_send_fail(phone, **kwargs):
    return {
        'ok': False,
        'status_code': 400,
        'message_id': '',
        'error': {'error': {'code': 131047, 'message': 'Failed'}},
    }


def _assert_lock_released(send_run: MarketingCampaignSendRun) -> None:
    send_run.refresh_from_db()
    self_assert = send_run.active_simple_mailing_lock is None
    assert self_assert, f'lock still held on run #{send_run.pk} status={send_run.status}'
    assert not simple_mailing_has_active_run()


@override_settings(**LIVE_SIMPLE_SETTINGS)
class SimpleMailingLockReleaseTests(TestCase):
    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_completed_run_releases_lock(self, mocked):
        mocked.side_effect = _mock_send_ok
        user = _make_n_request_buyers(2)
        send_run = _launch_simple(user=user, count=2)
        process_marketing_live_send_batch()
        send_run.refresh_from_db()
        self.assertEqual(send_run.status, SEND_RUN_STATUS_COMPLETED)
        _assert_lock_released(send_run)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_partial_run_releases_lock(self, mocked):
        mocked.side_effect = [_mock_send_ok('77000000001'), _mock_send_fail('77000000002')]
        user = _make_n_request_buyers(2)
        send_run = _launch_simple(user=user, count=2)
        process_marketing_live_send_batch()
        send_run.refresh_from_db()
        self.assertEqual(send_run.status, SEND_RUN_STATUS_PARTIAL)
        _assert_lock_released(send_run)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_failed_run_releases_lock(self, mocked):
        mocked.side_effect = _mock_send_fail
        user = _make_n_request_buyers(2)
        send_run = _launch_simple(user=user, count=2)
        process_marketing_live_send_batch()
        send_run.refresh_from_db()
        self.assertEqual(send_run.status, SEND_RUN_STATUS_FAILED)
        _assert_lock_released(send_run)

    def test_cancelled_run_releases_lock(self):
        user = _make_n_request_buyers(2)
        send_run = _launch_simple(user=user, count=2)
        cancel_live_send_run(send_run.pk)
        send_run.refresh_from_db()
        self.assertEqual(send_run.status, SEND_RUN_STATUS_CANCELLED)
        _assert_lock_released(send_run)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_last_message_failed_releases_lock(self, mocked):
        mocked.side_effect = [_mock_send_ok('77000000001'), _mock_send_fail('77000000002')]
        user = _make_n_request_buyers(2)
        send_run = _launch_simple(user=user, count=2)
        process_marketing_live_send_batch()
        last = send_run.messages.order_by('position_number').last()
        self.assertEqual(last.status, MESSAGE_STATUS_FAILED)
        _assert_lock_released(send_run)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_last_message_skipped_releases_lock(self, mocked):
        mocked.side_effect = _mock_send_ok
        user = _make_n_request_buyers(2)
        send_run = _launch_simple(user=user, count=2)
        target = send_run.messages.order_by('position_number').last()
        buyer = BuyerContact.objects.get(phone_normalized=target.phone_normalized)
        ContactConsent.objects.filter(buyer=buyer).update(status=CONTACT_CONSENT_STATUS_REVOKED)
        process_marketing_live_send_batch()
        target.refresh_from_db()
        self.assertEqual(target.status, MESSAGE_STATUS_SKIPPED)
        _assert_lock_released(send_run)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_all_failed_or_skipped_releases_lock(self, mocked):
        user = _make_n_request_buyers(2)
        send_run = _launch_simple(user=user, count=2)
        for message in send_run.messages.filter(status=MESSAGE_STATUS_QUEUED):
            buyer = BuyerContact.objects.get(phone_normalized=message.phone_normalized)
            ContactConsent.objects.filter(buyer=buyer).update(status=CONTACT_CONSENT_STATUS_REVOKED)
        process_marketing_live_send_batch()
        send_run.refresh_from_db()
        self.assertEqual(send_run.status, SEND_RUN_STATUS_FAILED)
        self.assertEqual(send_run.messages.filter(status=MESSAGE_STATUS_SKIPPED).count(), 2)
        mocked.assert_not_called()
        _assert_lock_released(send_run)

    def test_launch_exception_rolls_back_run_and_lock(self):
        user = _make_n_request_buyers(2)
        template = make_test_send_template(user)
        campaigns_before = MarketingCampaign.objects.count()
        runs_before = MarketingCampaignSendRun.objects.count()
        original_create = MarketingCampaignMessage.objects.create
        calls = {'count': 0}

        def failing_create(*args, **kwargs):
            calls['count'] += 1
            if calls['count'] >= 2:
                raise RuntimeError('simulated message create failure')
            return original_create(*args, **kwargs)

        with patch.object(MarketingCampaignMessage.objects, 'create', side_effect=failing_create):
            with self.assertRaises(RuntimeError):
                launch_simple_mailing(
                    draft=_launch_draft(count=2, template_id=template.pk),
                    template=template,
                    created_by=user,
                    launch_key=str(uuid.uuid4()),
                )

        self.assertEqual(MarketingCampaign.objects.count(), campaigns_before)
        self.assertEqual(MarketingCampaignSendRun.objects.count(), runs_before)
        self.assertFalse(
            MarketingCampaignSendRun.objects.filter(
                active_simple_mailing_lock=ACTIVE_SIMPLE_MAILING_LOCK_VALUE,
            ).exists(),
        )

    def test_stuck_processing_keeps_lock(self):
        user = _make_n_request_buyers(1)
        send_run = _launch_simple(user=user, count=1)
        message = send_run.messages.get()
        message.status = MESSAGE_STATUS_PROCESSING
        message.save(update_fields=['status'])
        send_run.status = SEND_RUN_STATUS_RUNNING
        send_run.save(update_fields=['status'])

        _finalize_send_run(send_run.pk)

        send_run.refresh_from_db()
        self.assertEqual(send_run.active_simple_mailing_lock, ACTIVE_SIMPLE_MAILING_LOCK_VALUE)
        self.assertEqual(send_run.status, SEND_RUN_STATUS_RUNNING)

    def test_stuck_audit_finalize_releases_lock(self):
        user = _make_n_request_buyers(1)
        send_run = _launch_simple(user=user, count=1)
        message = send_run.messages.get()
        message.status = MESSAGE_STATUS_PROCESSING
        message.save(update_fields=['status'])

        mark_stuck_live_processing_as_delivery_unknown(message_ids=[message.pk])

        send_run.refresh_from_db()
        message.refresh_from_db()
        self.assertEqual(message.status, MESSAGE_STATUS_FAILED)
        self.assertEqual(send_run.status, SEND_RUN_STATUS_FAILED)
        _assert_lock_released(send_run)

    def test_orphaned_lock_cleared_on_terminal_re_finalize(self):
        user = _make_n_request_buyers(1)
        send_run = _launch_simple(user=user, count=1)
        message = send_run.messages.get()
        message.status = MESSAGE_STATUS_SENT
        message.save(update_fields=['status'])
        send_run.status = SEND_RUN_STATUS_COMPLETED
        send_run.save(update_fields=['status'])

        _finalize_send_run(send_run.pk)

        _assert_lock_released(send_run)

    def test_launch_all_skipped_at_queue_does_not_leave_lock(self):
        user = _make_n_request_buyers(1)
        template = make_test_send_template(user)
        with patch(
            'marketing.services.simple_mailing.launch.evaluate_simple_mailing_phone',
            return_value=(False, 'test_contact'),
        ):
            with self.assertRaises(SimpleMailingLaunchError):
                launch_simple_mailing(
                    draft=_launch_draft(count=1, template_id=template.pk),
                    template=template,
                    created_by=user,
                    launch_key=str(uuid.uuid4()),
                )
        self.assertFalse(
            MarketingCampaignSendRun.objects.filter(
                active_simple_mailing_lock=ACTIVE_SIMPLE_MAILING_LOCK_VALUE,
            ).exists(),
        )
