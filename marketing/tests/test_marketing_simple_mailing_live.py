from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    BuyerContact,
    ContactConsent,
)
from marketing.models import MarketingAudience, MarketingCampaignMessage, MarketingCampaignSendRun
from marketing.services.campaigns.live_processor import cancel_live_send_run, process_marketing_live_send_batch
from marketing.services.campaigns.live_send import create_live_send_queue
from marketing.services.campaigns.live_simple_waves import get_next_eligible_simple_mailing_wave
from marketing.services.campaigns.live_send_validation import LiveSendValidationError
from marketing.services.campaigns.send_constants import (
    ERROR_CODE_DELIVERY_UNKNOWN,
    MESSAGE_STATUS_FAILED,
    MESSAGE_STATUS_PROCESSING,
    MESSAGE_STATUS_QUEUED,
    MESSAGE_STATUS_SENT,
    MESSAGE_STATUS_SKIPPED,
    RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
    RECIPIENT_SCOPE_CONTROL_ONLY,
    WORKFLOW_TYPE_LEGACY,
    WORKFLOW_TYPE_SIMPLE_MAILING,
)
from marketing.services.simple_mailing.constants import (
    RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
)
from marketing.services.simple_mailing.launch import (
    SimpleMailingLaunchError,
    build_simple_mailing_audience_name,
    launch_simple_mailing,
)
from marketing.services.simple_mailing.waves import compute_wave_schedule
from marketing.tests.test_marketing_audiences import grant_consent, grant_marketing_permission, make_buyer
from marketing.tests.test_marketing_campaign_live_send import LIVE_SETTINGS, setup_ready_live_campaign
from marketing.tests.test_marketing_campaign_send import ensure_portal_access, make_test_send_template
from marketing.tests.test_marketing_simple_mailing import make_request


LIVE_SIMPLE_SETTINGS = {
    **LIVE_SETTINGS,
    'MARKETING_SIMPLE_WAVE_SIZE': 10,
    'MARKETING_SIMPLE_WAVE_INTERVAL_MINUTES': 5,
    'MARKETING_LIVE_SEND_INTERVAL_SECONDS': 0,
}


def _make_parts_buyer_template(user: User):
    return make_test_send_template(user)


def _make_n_request_buyers(
    count: int,
    *,
    consent_status=CONTACT_CONSENT_STATUS_GRANTED,
) -> None:
    for _ in range(count):
        buyer = make_buyer(is_test_contact=False)
        make_request(buyer, brand='Toyota')
        if consent_status:
            grant_consent(buyer, consent_status)
        ensure_portal_access(buyer)


def _launch_draft(*, count: int, template_id: int, recipient_scope=RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS) -> dict:
    return {
        'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
        'recipient_scope': recipient_scope,
        'all_brands': True,
        'brands': [],
        'count': count,
        'template_id': template_id,
    }


def _make_control_buyer() -> BuyerContact:
    buyer = make_buyer(is_control_recipient=True, is_test_contact=False)
    make_request(buyer, brand='Toyota')
    grant_consent(buyer, CONTACT_CONSENT_STATUS_GRANTED)
    ensure_portal_access(buyer)
    return buyer


def _mock_send_ok(phone, **kwargs):
    return {
        'ok': True,
        'status_code': 200,
        'message_id': f'wamid.simple.{phone[-4:]}',
        'error': None,
    }


@override_settings(**LIVE_SIMPLE_SETTINGS)
class SimpleMailingWaveScheduleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('simple-live', password='secret', is_staff=True)

    def test_n37_wave_schedule(self):
        t0 = timezone.now()
        rows = compute_wave_schedule(
            total_count=37,
            t0=t0,
            wave_size=10,
            interval_minutes=5,
        )
        self.assertEqual(len(rows), 37)
        wave_counts = {1: 0, 2: 0, 3: 0, 4: 0}
        for position, wave_number, scheduled_at in rows:
            wave_counts[wave_number] += 1
            expected_wave = ((position - 1) // 10) + 1
            self.assertEqual(wave_number, expected_wave)
            expected_at = t0 + timedelta(minutes=5 * (expected_wave - 1))
            self.assertEqual(scheduled_at, expected_at)
        self.assertEqual(wave_counts, {1: 10, 2: 10, 3: 10, 4: 7})

    def test_launch_creates_waves_in_db(self):
        _make_n_request_buyers(37)
        template = _make_parts_buyer_template(self.user)
        result = launch_simple_mailing(
            draft=_launch_draft(count=37, template_id=template.pk),
            template=template,
            created_by=self.user,
            launch_key=str(uuid.uuid4()),
        )
        send_run = MarketingCampaignSendRun.objects.get(pk=result.send_run_id)
        self.assertEqual(send_run.workflow_type, WORKFLOW_TYPE_SIMPLE_MAILING)
        for wave_number, expected in [(1, 10), (2, 10), (3, 10), (4, 7)]:
            self.assertEqual(
                send_run.messages.filter(wave_number=wave_number).count(),
                expected,
            )


@override_settings(**LIVE_SIMPLE_SETTINGS)
class SimpleMailingProcessorWaveTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('simple-proc', password='secret', is_staff=True)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_processor_at_t0_only_wave1(self, mocked):
        mocked.side_effect = _mock_send_ok
        _make_n_request_buyers(37)
        template = _make_parts_buyer_template(self.user)
        result = launch_simple_mailing(
            draft=_launch_draft(count=37, template_id=template.pk),
            template=template,
            created_by=self.user,
            launch_key=str(uuid.uuid4()),
        )
        batch = process_marketing_live_send_batch()
        self.assertEqual(batch.processed_count, 10)
        send_run = MarketingCampaignSendRun.objects.get(pk=result.send_run_id)
        self.assertEqual(
            send_run.messages.filter(wave_number=1, status=MESSAGE_STATUS_SENT).count(),
            10,
        )
        self.assertEqual(
            send_run.messages.filter(wave_number=2, status=MESSAGE_STATUS_QUEUED).count(),
            10,
        )
        self.assertEqual(mocked.call_count, 10)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_wave2_not_before_interval_after_wave1(self, mocked):
        mocked.side_effect = _mock_send_ok
        _make_n_request_buyers(20)
        template = _make_parts_buyer_template(self.user)
        result = launch_simple_mailing(
            draft=_launch_draft(count=20, template_id=template.pk),
            template=template,
            created_by=self.user,
            launch_key=str(uuid.uuid4()),
        )
        send_run = MarketingCampaignSendRun.objects.get(pk=result.send_run_id)
        process_marketing_live_send_batch()
        t_after_wave1 = timezone.now()
        eligible = get_next_eligible_simple_mailing_wave(send_run, now=t_after_wave1)
        self.assertIsNone(eligible)
        eligible_later = get_next_eligible_simple_mailing_wave(
            send_run,
            now=t_after_wave1 + timedelta(minutes=5),
        )
        self.assertEqual(eligible_later, 2)


@override_settings(**LIVE_SIMPLE_SETTINGS)
class SimpleMailingDelayedProcessorTests(TestCase):
    def test_wave3_blocked_until_actual_wave2_terminal_plus_interval(self):
        _make_n_request_buyers(37)
        user = User.objects.create_user('delayed', password='secret', is_staff=True)
        template = _make_parts_buyer_template(user)
        t0 = timezone.now()
        result = launch_simple_mailing(
            draft=_launch_draft(count=37, template_id=template.pk),
            template=template,
            created_by=user,
            launch_key=str(uuid.uuid4()),
        )
        send_run = MarketingCampaignSendRun.objects.get(pk=result.send_run_id)

        wave1_terminal = t0
        send_run.messages.filter(wave_number=1).update(
            status=MESSAGE_STATUS_SENT,
            sent_at=wave1_terminal,
            attempted_at=wave1_terminal,
        )

        wave2_late = t0 + timedelta(minutes=12)
        send_run.messages.filter(wave_number=2).update(
            status=MESSAGE_STATUS_SENT,
            sent_at=wave2_late,
            attempted_at=wave2_late,
        )

        too_early = t0 + timedelta(minutes=13)
        self.assertIsNone(
            get_next_eligible_simple_mailing_wave(send_run, now=too_early),
        )

        allowed_at = t0 + timedelta(minutes=17)
        self.assertEqual(
            get_next_eligible_simple_mailing_wave(send_run, now=allowed_at),
            3,
        )


@override_settings(**LIVE_SIMPLE_SETTINGS)
class SimpleMailingFailureWaveTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('fail-wave', password='secret', is_staff=True)

    def test_wave1_mixed_terminal_allows_wave2_after_interval(self):
        _make_n_request_buyers(20)
        template = _make_parts_buyer_template(self.user)
        result = launch_simple_mailing(
            draft=_launch_draft(count=20, template_id=template.pk),
            template=template,
            created_by=self.user,
            launch_key=str(uuid.uuid4()),
        )
        send_run = MarketingCampaignSendRun.objects.get(pk=result.send_run_id)
        wave1 = list(send_run.messages.filter(wave_number=1).order_by('position_number'))
        t0 = timezone.now()
        for message in wave1[:8]:
            message.status = MESSAGE_STATUS_SENT
            message.sent_at = t0
            message.attempted_at = t0
            message.save(update_fields=['status', 'sent_at', 'attempted_at'])
        wave1[8].status = MESSAGE_STATUS_FAILED
        wave1[8].attempted_at = t0
        wave1[8].save(update_fields=['status', 'attempted_at'])
        wave1[9].status = MESSAGE_STATUS_FAILED
        wave1[9].error_code = ERROR_CODE_DELIVERY_UNKNOWN
        wave1[9].attempted_at = t0
        wave1[9].save(update_fields=['status', 'error_code', 'attempted_at'])

        self.assertIsNone(
            get_next_eligible_simple_mailing_wave(send_run, now=t0 + timedelta(minutes=4)),
        )
        self.assertEqual(
            get_next_eligible_simple_mailing_wave(send_run, now=t0 + timedelta(minutes=5)),
            2,
        )
        self.assertEqual(
            send_run.messages.filter(wave_number=1, status=MESSAGE_STATUS_QUEUED).count(),
            0,
        )


@override_settings(**LIVE_SIMPLE_SETTINGS)
class SimpleMailingRevokeBetweenWavesTests(TestCase):
    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_revoked_recipient_skipped_in_wave3(self, mocked):
        mocked.side_effect = _mock_send_ok
        user = User.objects.create_user('revoke', password='secret', is_staff=True)
        buyers = []
        for _ in range(25):
            buyer = make_buyer(is_test_contact=False)
            make_request(buyer, brand='Toyota')
            grant_consent(buyer, CONTACT_CONSENT_STATUS_UNKNOWN)
            ensure_portal_access(buyer)
            buyers.append(buyer)

        template = _make_parts_buyer_template(user)
        result = launch_simple_mailing(
            draft=_launch_draft(count=25, template_id=template.pk),
            template=template,
            created_by=user,
            launch_key=str(uuid.uuid4()),
        )
        send_run = MarketingCampaignSendRun.objects.get(pk=result.send_run_id)
        t0 = timezone.now()
        wave12_done = t0 - timedelta(minutes=15)
        send_run.messages.filter(wave_number__lte=2).update(
            status=MESSAGE_STATUS_SENT,
            sent_at=wave12_done,
            attempted_at=wave12_done,
        )
        send_run.messages.filter(wave_number=3).update(scheduled_at=wave12_done)

        target = send_run.messages.filter(wave_number=3).first()
        buyer = BuyerContact.objects.get(phone_normalized=target.phone_normalized)
        ContactConsent.objects.filter(buyer=buyer).update(status=CONTACT_CONSENT_STATUS_REVOKED)

        eligible = get_next_eligible_simple_mailing_wave(
            send_run,
            now=timezone.now(),
        )
        self.assertEqual(eligible, 3)

        with patch('marketing.services.campaigns.live_processor.time.sleep'):
            batch = process_marketing_live_send_batch(batch_size=10)
        self.assertEqual(batch.skipped_count, 1)
        self.assertEqual(
            send_run.messages.filter(
                wave_number=3,
                status=MESSAGE_STATUS_SKIPPED,
                error_code='consent_revoked',
            ).count(),
            1,
        )
        self.assertEqual(mocked.call_count, 4)


@override_settings(**LIVE_SIMPLE_SETTINGS)
class SimpleMailingLaunchIdempotencyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('idem', password='secret', is_staff=True)

    def test_double_launch_same_key_one_run(self):
        _make_n_request_buyers(3)
        template = _make_parts_buyer_template(self.user)
        draft = _launch_draft(count=3, template_id=template.pk)
        key = str(uuid.uuid4())
        first = launch_simple_mailing(
            draft=draft,
            template=template,
            created_by=self.user,
            launch_key=key,
        )
        second = launch_simple_mailing(
            draft=draft,
            template=template,
            created_by=self.user,
            launch_key=key,
        )
        self.assertTrue(second.idempotent_replay)
        self.assertEqual(first.send_run_id, second.send_run_id)
        self.assertEqual(MarketingCampaignSendRun.objects.count(), 1)

    def test_second_active_simple_mailing_blocked(self):
        _make_n_request_buyers(3)
        template = _make_parts_buyer_template(self.user)
        launch_simple_mailing(
            draft=_launch_draft(count=3, template_id=template.pk),
            template=template,
            created_by=self.user,
            launch_key=str(uuid.uuid4()),
        )
        with self.assertRaises(SimpleMailingLaunchError):
            launch_simple_mailing(
                draft=_launch_draft(count=3, template_id=template.pk),
                template=template,
                created_by=self.user,
                launch_key=str(uuid.uuid4()),
            )


@override_settings(**LIVE_SIMPLE_SETTINGS)
class SimpleMailingLaunchZeroMetaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('zero-meta', password='secret', is_staff=True)

    @patch('core.whatsapp_template_sender.send_whatsapp_template_message')
    def test_launch_zero_meta_calls(self, mocked):
        _make_n_request_buyers(5)
        template = _make_parts_buyer_template(self.user)
        launch_simple_mailing(
            draft=_launch_draft(count=5, template_id=template.pk),
            template=template,
            created_by=self.user,
            launch_key=str(uuid.uuid4()),
        )
        mocked.assert_not_called()


@override_settings(MARKETING_WHATSAPP_SEND_MODE='OFF')
class SimpleMailingOffModeTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        self.user = User.objects.create_user('off-mode', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='off-mode', password='secret')

    def _prepare_confirm_session(self):
        buyer = make_buyer()
        make_request(buyer, brand='Toyota')
        grant_consent(buyer)
        ensure_portal_access(buyer)
        session = self.client.session
        session['marketing_simple_mailing_draft'] = {
            'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            'all_brands': True,
            'brands': [],
            'count': 1,
            'launch_key': str(uuid.uuid4()),
        }
        template = _make_parts_buyer_template(self.user)
        session['marketing_simple_mailing_draft']['template_id'] = template.pk
        session.save()

    def test_confirm_page_disabled_send(self):
        self._prepare_confirm_session()
        response = self.client.get(reverse('marketing:new_mailing_confirm'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'disabled')
        self.assertContains(response, 'Отправка отключена. Режим: OFF')

    def test_launch_post_blocked_server_side(self):
        self._prepare_confirm_session()
        response = self.client.post(reverse('marketing:new_mailing_confirm'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(MarketingCampaignSendRun.objects.count(), 0)


@override_settings(**LIVE_SETTINGS)
class SimpleMailingLegacyRegressionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('legacy', password='secret', is_staff=True)

    def test_legacy_run_has_workflow_type_legacy(self):
        campaign = setup_ready_live_campaign(self.user, recipient_count=2)
        result = create_live_send_queue(
            campaign.pk,
            created_by=self.user,
            confirmation_text='LIVE',
        )
        send_run = MarketingCampaignSendRun.objects.get(pk=result.send_run_id)
        self.assertEqual(send_run.workflow_type, WORKFLOW_TYPE_LEGACY)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_legacy_unknown_consent_skipped_at_send(self, mocked):
        mocked.side_effect = _mock_send_ok
        campaign = setup_ready_live_campaign(self.user, recipient_count=1)
        create_live_send_queue(
            campaign.pk,
            created_by=self.user,
            confirmation_text='LIVE',
        )
        buyer = BuyerContact.objects.filter(is_test_contact=False).first()
        ContactConsent.objects.filter(buyer=buyer).update(
            status=CONTACT_CONSENT_STATUS_UNKNOWN,
        )
        batch = process_marketing_live_send_batch()
        self.assertEqual(batch.skipped_count, 1)
        mocked.assert_not_called()

    def test_legacy_max_recipients_still_enforced(self):
        campaign = setup_ready_live_campaign(self.user, recipient_count=11)
        with self.assertRaises(LiveSendValidationError):
            create_live_send_queue(
                campaign.pk,
                created_by=self.user,
                confirmation_text='LIVE',
            )


@override_settings(**LIVE_SIMPLE_SETTINGS)
class SimpleMailingUnknownConsentLaunchTests(TestCase):
    def test_unknown_consent_queued_at_launch(self):
        user = User.objects.create_user('unknown', password='secret', is_staff=True)
        _make_n_request_buyers(2, consent_status=CONTACT_CONSENT_STATUS_UNKNOWN)
        template = _make_parts_buyer_template(user)
        result = launch_simple_mailing(
            draft=_launch_draft(count=2, template_id=template.pk),
            template=template,
            created_by=user,
            launch_key=str(uuid.uuid4()),
        )
        send_run = MarketingCampaignSendRun.objects.get(pk=result.send_run_id)
        self.assertEqual(result.queued_count, 2)
        self.assertEqual(
            send_run.messages.filter(status=MESSAGE_STATUS_QUEUED).count(),
            2,
        )


@override_settings(**LIVE_SIMPLE_SETTINGS)
class SimpleMailingProcessorConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_two_batches_do_not_double_claim(self, mocked):
        mocked.side_effect = _mock_send_ok
        user = User.objects.create_user('conc', password='secret', is_staff=True)
        _make_n_request_buyers(5)
        template = _make_parts_buyer_template(user)
        launch_simple_mailing(
            draft=_launch_draft(count=5, template_id=template.pk),
            template=template,
            created_by=user,
            launch_key=str(uuid.uuid4()),
        )
        process_marketing_live_send_batch(batch_size=3)
        process_marketing_live_send_batch(batch_size=3)
        processing_count = MarketingCampaignMessage.objects.filter(
            status=MESSAGE_STATUS_PROCESSING,
        ).count()
        self.assertEqual(processing_count, 0)
        self.assertLessEqual(mocked.call_count, 5)


class SimpleMailingAudienceNameHelperTests(TestCase):
    def test_build_name_includes_launch_key_suffix(self):
        launch_key = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890'
        name = build_simple_mailing_audience_name(
            campaign_name='Control — All — 21.07.2026',
            launch_key=launch_key,
        )
        self.assertIn('[a1b2c3d4]', name)
        self.assertTrue(name.startswith('[Simple mailing] '))

    def test_build_name_respects_max_length(self):
        long_campaign = 'X' * 300
        launch_key = str(uuid.uuid4())
        name = build_simple_mailing_audience_name(
            campaign_name=long_campaign,
            launch_key=launch_key,
        )
        self.assertLessEqual(len(name), 200)
        self.assertIn(f'[{launch_key.replace("-", "")[:8]}]', name)


@override_settings(**LIVE_SIMPLE_SETTINGS)
class SimpleMailingDuplicateAudienceNameTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('dup-aud', password='secret', is_staff=True)

    def test_second_control_only_launch_same_day_succeeds(self):
        _make_control_buyer()
        template = make_test_send_template(self.user)
        draft = _launch_draft(
            count=1,
            template_id=template.pk,
            recipient_scope=RECIPIENT_SCOPE_CONTROL_ONLY,
        )
        launch_key_1 = str(uuid.uuid4())
        first = launch_simple_mailing(
            draft=draft,
            template=template,
            created_by=self.user,
            launch_key=launch_key_1,
        )
        cancel_live_send_run(first.send_run_id)

        launch_key_2 = str(uuid.uuid4())
        second = launch_simple_mailing(
            draft=draft,
            template=template,
            created_by=self.user,
            launch_key=launch_key_2,
        )

        self.assertNotEqual(first.send_run_id, second.send_run_id)
        self.assertEqual(MarketingCampaignSendRun.objects.count(), 2)
        audiences = list(
            MarketingAudience.objects.exclude(
                name='AG Parts — продавцы по маркам оптового ассортимента — 08.2026',
            ).order_by('id')
        )
        self.assertEqual(len(audiences), 2)
        self.assertNotEqual(audiences[0].name, audiences[1].name)
        self.assertLessEqual(len(audiences[0].name), 200)
        self.assertLessEqual(len(audiences[1].name), 200)
        self.assertIn(launch_key_1.replace('-', '')[:8], audiences[0].name)
        self.assertIn(launch_key_2.replace('-', '')[:8], audiences[1].name)

    def test_same_launch_key_is_idempotent(self):
        _make_control_buyer()
        template = make_test_send_template(self.user)
        draft = _launch_draft(
            count=1,
            template_id=template.pk,
            recipient_scope=RECIPIENT_SCOPE_CONTROL_ONLY,
        )
        launch_key = str(uuid.uuid4())
        first = launch_simple_mailing(
            draft=draft,
            template=template,
            created_by=self.user,
            launch_key=launch_key,
        )
        second = launch_simple_mailing(
            draft=draft,
            template=template,
            created_by=self.user,
            launch_key=launch_key,
        )
        self.assertTrue(second.idempotent_replay)
        self.assertEqual(first.send_run_id, second.send_run_id)
        self.assertEqual(MarketingCampaignSendRun.objects.count(), 1)
        self.assertEqual(
            MarketingAudience.objects.exclude(
                name='AG Parts — продавцы по маркам оптового ассортимента — 08.2026',
            ).count(),
            1,
        )


@override_settings(**LIVE_SIMPLE_SETTINGS)
class SimpleMailingConfirmDuplicateAudienceNameTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        self.user = User.objects.create_user('dup-view', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='dup-view', password='secret')
        self.confirm_url = reverse('marketing:new_mailing_confirm')
        self.history_url = reverse('marketing:history')
        self.template = make_test_send_template(self.user)
        _make_control_buyer()

    def _prepare_confirm_session(self, *, launch_key: str) -> None:
        session = self.client.session
        session['marketing_simple_mailing_draft'] = {
            'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            'recipient_scope': RECIPIENT_SCOPE_CONTROL_ONLY,
            'all_brands': True,
            'brands': [],
            'count': 1,
            'template_id': self.template.pk,
            'launch_key': launch_key,
        }
        session.save()

    def test_confirm_post_second_launch_same_day_not_500(self):
        launch_key_1 = str(uuid.uuid4())
        self._prepare_confirm_session(launch_key=launch_key_1)
        first_response = self.client.post(self.confirm_url)
        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(first_response.url, self.history_url)
        self.assertEqual(MarketingCampaignSendRun.objects.count(), 1)
        first_run = MarketingCampaignSendRun.objects.get()
        cancel_live_send_run(first_run.pk)

        launch_key_2 = str(uuid.uuid4())
        self._prepare_confirm_session(launch_key=launch_key_2)
        second_response = self.client.post(self.confirm_url)
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(second_response.url, self.history_url)
        self.assertEqual(MarketingCampaignSendRun.objects.count(), 2)
        audiences = list(
            MarketingAudience.objects.exclude(
                name='AG Parts — продавцы по маркам оптового ассортимента — 08.2026',
            ).order_by('id')
        )
        self.assertEqual(len(audiences), 2)
        self.assertNotEqual(audiences[0].name, audiences[1].name)
        self.assertLessEqual(len(audiences[0].name), 200)
        self.assertLessEqual(len(audiences[1].name), 200)
