from __future__ import annotations

import uuid
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    BUYER_CONTACT_STATUS_ACTIVE,
    BUYER_CONTACT_STATUS_BLOCKED,
    BUYER_CONTACT_STATUS_INVALID_PHONE,
    BUYER_CONTACT_STATUS_UNSUBSCRIBED,
    CONTACT_CONSENT_STATUS_GRANTED,
    BuyerContact,
    Seller,
)
from marketing.models import (
    MarketingCampaign,
    MarketingCampaignMessage,
    MarketingCampaignRecipient,
    MarketingCampaignSendRun,
)
from marketing.services.campaigns.constants import (
    CHANNEL_WHATSAPP,
    ELIGIBILITY_ELIGIBLE,
    STATUS_AUDIENCE_PREPARED,
    PURPOSE_PARTS_BUYERS,
)
from marketing.services.campaigns.live_simple_waves import get_next_eligible_simple_mailing_wave
from marketing.services.campaigns.send_constants import (
    MESSAGE_STATUS_QUEUED,
    MESSAGE_STATUS_SKIPPED,
    RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
    RECIPIENT_SCOPE_CONTROL_ONLY,
    SEND_RUN_STATUS_PARTIAL,
    WORKFLOW_TYPE_SIMPLE_MAILING,
)
from marketing.services.simple_mailing.consent import (
    SKIP_REASON_BLOCKED,
    SKIP_REASON_CONSENT_REVOKED,
    SKIP_REASON_INVALID_PHONE,
    SKIP_REASON_TEST_CONTACT,
    SKIP_REASON_UNSUBSCRIBED,
    evaluate_simple_mailing_phone,
    recheck_simple_mailing_phone,
    recheck_simple_mailing_recipient,
)
from marketing.services.simple_mailing.constants import (
    DEFAULT_RECIPIENT_SCOPE,
    RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
)
from marketing.services.simple_mailing.launch import SimpleMailingLaunchError, launch_simple_mailing
from marketing.services.simple_mailing.launch_recipients import resolve_simple_mailing_launch_recipients
from marketing.services.simple_mailing.recipients import resolve_simple_mailing_recipients
from marketing.services.simple_mailing.waves import compute_wave_schedule
from marketing.tests.test_marketing_campaigns import make_audience
from marketing.tests.test_marketing_audiences import grant_consent, grant_marketing_permission, make_buyer, next_phone
from marketing.tests.test_marketing_campaign_live_send import LIVE_SETTINGS
from marketing.tests.test_marketing_campaign_send import ensure_portal_access, make_test_send_template
from marketing.tests.test_marketing_simple_mailing import make_request


LIVE_SIMPLE_SETTINGS = {
    **LIVE_SETTINGS,
    'MARKETING_SIMPLE_WAVE_SIZE': 10,
    'MARKETING_SIMPLE_WAVE_INTERVAL_MINUTES': 5,
    'MARKETING_LIVE_SEND_INTERVAL_SECONDS': 0,
}


def _make_control_buyer(**kwargs) -> BuyerContact:
    defaults = {
        'is_control_recipient': True,
        'is_test_contact': False,
    }
    defaults.update(kwargs)
    buyer = make_buyer(**defaults)
    grant_consent(buyer, CONTACT_CONSENT_STATUS_GRANTED)
    ensure_portal_access(buyer)
    return buyer


def _make_ordinary_buyer(*, brand: str = 'Toyota') -> BuyerContact:
    buyer = make_buyer(is_test_contact=False)
    make_request(buyer, brand=brand)
    grant_consent(buyer, CONTACT_CONSENT_STATUS_GRANTED)
    ensure_portal_access(buyer)
    return buyer


def _launch_draft(*, count: int, template_id: int, recipient_scope: str) -> dict:
    return {
        'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
        'recipient_scope': recipient_scope,
        'all_brands': True,
        'brands': [],
        'count': count,
        'template_id': template_id,
    }


class BuyerContactControlFieldTests(TestCase):
    def test_default_is_control_recipient_false(self):
        buyer = make_buyer()
        self.assertFalse(buyer.is_control_recipient)


class ControlOnlyRecipientsTests(TestCase):
    def test_two_controls_exactly_two_recipients(self):
        _make_control_buyer()
        _make_control_buyer()
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            recipient_scope=RECIPIENT_SCOPE_CONTROL_ONLY,
        )
        self.assertEqual(result.ordinary_count, 0)
        self.assertEqual(result.control_count, 2)
        self.assertEqual(result.count, 2)

    def test_brand_filter_does_not_limit_controls(self):
        control = _make_control_buyer()
        _make_ordinary_buyer(brand='Toyota')
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            recipient_scope=RECIPIENT_SCOPE_CONTROL_ONLY,
            all_brands=False,
            brands=['BMW'],
        )
        launch_rows = resolve_simple_mailing_launch_recipients(
            recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            recipient_scope=RECIPIENT_SCOPE_CONTROL_ONLY,
            all_brands=False,
            brands=['BMW'],
        )
        phones = {row.phone_normalized for row in launch_rows}
        self.assertEqual(result.count, 1)
        self.assertIn(control.phone_normalized, phones)

    def test_ordinary_contacts_not_added(self):
        _make_control_buyer()
        _make_ordinary_buyer()
        _make_ordinary_buyer()
        launch_rows = resolve_simple_mailing_launch_recipients(
            recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            recipient_scope=RECIPIENT_SCOPE_CONTROL_ONLY,
        )
        self.assertEqual(len(launch_rows), 1)
        self.assertTrue(all(row.is_control_recipient for row in launch_rows))

    def test_zero_controls_launch_blocked(self):
        user = User.objects.create_user('control-zero', password='secret', is_staff=True)
        template = make_test_send_template(user)
        with self.assertRaises(SimpleMailingLaunchError):
            launch_simple_mailing(
                draft=_launch_draft(
                    count=0,
                    template_id=template.pk,
                    recipient_scope=RECIPIENT_SCOPE_CONTROL_ONLY,
                ),
                template=template,
                created_by=user,
                launch_key=str(uuid.uuid4()),
            )


class AudiencePlusControlsTests(TestCase):
    def test_12_ordinary_plus_2_controls_equals_14(self):
        for _ in range(12):
            _make_ordinary_buyer()
        _make_control_buyer()
        _make_control_buyer()
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            recipient_scope=RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
            all_brands=True,
        )
        self.assertEqual(result.ordinary_count, 12)
        self.assertEqual(result.control_count, 2)
        self.assertEqual(result.count, 14)

    def test_duplicate_control_phone_not_duplicated(self):
        ordinary = _make_ordinary_buyer()
        ordinary.is_control_recipient = True
        ordinary.save(update_fields=['is_control_recipient'])
        launch_rows = resolve_simple_mailing_launch_recipients(
            recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            recipient_scope=RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
            all_brands=True,
        )
        self.assertEqual(len(launch_rows), 1)

    def test_controls_added_independent_of_brands(self):
        control = _make_control_buyer()
        _make_ordinary_buyer(brand='Toyota')
        launch_rows = resolve_simple_mailing_launch_recipients(
            recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            recipient_scope=RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
            all_brands=False,
            brands=['BMW'],
        )
        phones = {row.phone_normalized for row in launch_rows}
        self.assertIn(control.phone_normalized, phones)
        self.assertEqual(len(launch_rows), 1)


class ControlTestSemanticsTests(TestCase):
    def test_test_true_control_false_excluded(self):
        make_request(make_buyer(is_test_contact=True), brand='Toyota')
        result = resolve_simple_mailing_recipients(
            recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            recipient_scope=RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
            all_brands=True,
        )
        self.assertEqual(result.count, 0)
        test_buyer = make_buyer(is_test_contact=True)
        eligible, reason = evaluate_simple_mailing_phone(
            phone_normalized=test_buyer.phone_normalized,
            is_test=True,
            is_control=False,
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, SKIP_REASON_TEST_CONTACT)

    def test_test_true_control_true_allowed_for_simple_mailing(self):
        buyer = _make_control_buyer(is_test_contact=True)
        launch_rows = resolve_simple_mailing_launch_recipients(
            recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
            recipient_scope=RECIPIENT_SCOPE_CONTROL_ONLY,
        )
        self.assertEqual(len(launch_rows), 1)
        eligible, reason = evaluate_simple_mailing_phone(
            phone_normalized=buyer.phone_normalized,
            is_test=True,
            is_control=True,
        )
        self.assertTrue(eligible)
        self.assertEqual(reason, '')

    def test_legacy_recheck_without_control_still_blocks_test(self):
        buyer = make_buyer(is_test_contact=True)
        eligible, reason = recheck_simple_mailing_phone(phone_normalized=buyer.phone_normalized)
        self.assertFalse(eligible)
        self.assertEqual(reason, SKIP_REASON_TEST_CONTACT)


class ControlHardExclusionsTests(TestCase):
    def test_revoked_control_excluded(self):
        from core.models import (
            CONTACT_CONSENT_CHANNEL_WHATSAPP,
            CONTACT_CONSENT_PURPOSE_MARKETING,
            CONTACT_CONSENT_STATUS_REVOKED,
            ContactConsent,
        )

        buyer = make_buyer(is_control_recipient=True)
        ContactConsent.objects.create(
            buyer=buyer,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_REVOKED,
            consented_at=timezone.now(),
            revoked_at=timezone.now(),
        )
        eligible, reason = evaluate_simple_mailing_phone(
            phone_normalized=buyer.phone_normalized,
            is_control=True,
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, SKIP_REASON_CONSENT_REVOKED)

    def test_unsubscribed_control_excluded(self):
        buyer = _make_control_buyer(status=BUYER_CONTACT_STATUS_UNSUBSCRIBED)
        eligible, reason = evaluate_simple_mailing_phone(
            phone_normalized=buyer.phone_normalized,
            is_control=True,
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, SKIP_REASON_UNSUBSCRIBED)

    def test_blocked_control_excluded(self):
        buyer = _make_control_buyer(status=BUYER_CONTACT_STATUS_BLOCKED)
        eligible, reason = evaluate_simple_mailing_phone(
            phone_normalized=buyer.phone_normalized,
            is_control=True,
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, SKIP_REASON_BLOCKED)

    def test_invalid_phone_control_excluded(self):
        buyer = _make_control_buyer(status=BUYER_CONTACT_STATUS_INVALID_PHONE, phone_normalized='')
        eligible, reason = evaluate_simple_mailing_phone(
            phone_normalized=buyer.phone_normalized,
            is_control=True,
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, SKIP_REASON_INVALID_PHONE)


@override_settings(**LIVE_SIMPLE_SETTINGS)
class ControlVariablesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('control-vars', password='secret', is_staff=True)

    def test_request_history_url_resolves_for_control(self):
        _make_control_buyer()
        template = make_test_send_template(self.user)
        result = launch_simple_mailing(
            draft=_launch_draft(
                count=1,
                template_id=template.pk,
                recipient_scope=RECIPIENT_SCOPE_CONTROL_ONLY,
            ),
            template=template,
            created_by=self.user,
            launch_key=str(uuid.uuid4()),
        )
        message = MarketingCampaignMessage.objects.get(send_run_id=result.send_run_id)
        self.assertEqual(message.status, MESSAGE_STATUS_QUEUED)
        self.assertIn('request_history_url', message.variables)

    def test_missing_variable_skipped_not_sent(self):
        from core.models import BuyerPortalAccess

        buyer = _make_control_buyer()
        BuyerPortalAccess.objects.filter(phone_normalized=buyer.phone_normalized).delete()
        template = make_test_send_template(self.user)
        with self.assertRaises(SimpleMailingLaunchError):
            launch_simple_mailing(
                draft=_launch_draft(
                    count=1,
                    template_id=template.pk,
                    recipient_scope=RECIPIENT_SCOPE_CONTROL_ONLY,
                ),
                template=template,
                created_by=self.user,
                launch_key=str(uuid.uuid4()),
            )
        self.assertEqual(MarketingCampaignMessage.objects.count(), 0)


@override_settings(**LIVE_SIMPLE_SETTINGS)
class ControlWaveTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('control-waves', password='secret', is_staff=True)

    def test_n2_one_wave_of_two(self):
        _make_control_buyer()
        _make_control_buyer()
        rows = compute_wave_schedule(total_count=2, t0=timezone.now(), wave_size=10, interval_minutes=5)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(wave == 1 for _, wave, _ in rows))
        template = make_test_send_template(self.user)
        result = launch_simple_mailing(
            draft=_launch_draft(
                count=2,
                template_id=template.pk,
                recipient_scope=RECIPIENT_SCOPE_CONTROL_ONLY,
            ),
            template=template,
            created_by=self.user,
            launch_key=str(uuid.uuid4()),
        )
        send_run = MarketingCampaignSendRun.objects.get(pk=result.send_run_id)
        self.assertEqual(send_run.messages.filter(wave_number=1).count(), 2)
        self.assertEqual(send_run.messages.filter(wave_number=2).count(), 0)

    def test_n14_wave_split_10_plus_4(self):
        for _ in range(12):
            _make_ordinary_buyer()
        _make_control_buyer()
        _make_control_buyer()
        template = make_test_send_template(self.user)
        result = launch_simple_mailing(
            draft=_launch_draft(
                count=14,
                template_id=template.pk,
                recipient_scope=RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
            ),
            template=template,
            created_by=self.user,
            launch_key=str(uuid.uuid4()),
        )
        send_run = MarketingCampaignSendRun.objects.get(pk=result.send_run_id)
        self.assertEqual(send_run.messages.filter(wave_number=1).count(), 10)
        self.assertEqual(send_run.messages.filter(wave_number=2).count(), 4)

    def test_five_minute_wave_gate_unchanged(self):
        from marketing.services.campaigns.send_constants import MESSAGE_STATUS_SENT

        for _ in range(20):
            _make_ordinary_buyer()
        template = make_test_send_template(self.user)
        result = launch_simple_mailing(
            draft=_launch_draft(
                count=20,
                template_id=template.pk,
                recipient_scope=RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
            ),
            template=template,
            created_by=self.user,
            launch_key=str(uuid.uuid4()),
        )
        send_run = MarketingCampaignSendRun.objects.get(pk=result.send_run_id)
        wave1_sent_at = timezone.now()
        send_run.messages.filter(wave_number=1).update(
            status=MESSAGE_STATUS_SENT,
            sent_at=wave1_sent_at,
        )
        t_after_wave1 = wave1_sent_at
        self.assertIsNone(get_next_eligible_simple_mailing_wave(send_run, now=t_after_wave1))
        self.assertEqual(
            get_next_eligible_simple_mailing_wave(
                send_run,
                now=t_after_wave1 + timedelta(minutes=5),
            ),
            2,
        )


@override_settings(**LIVE_SIMPLE_SETTINGS)
class ControlIdempotencyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('control-idem', password='secret', is_staff=True)

    def test_duplicate_launch_no_duplicate_run(self):
        _make_control_buyer()
        template = make_test_send_template(self.user)
        launch_key = str(uuid.uuid4())
        draft = _launch_draft(
            count=1,
            template_id=template.pk,
            recipient_scope=RECIPIENT_SCOPE_CONTROL_ONLY,
        )
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

    def test_active_lock_shared_across_recipient_scopes(self):
        _make_control_buyer()
        for _ in range(3):
            _make_ordinary_buyer()
        template = make_test_send_template(self.user)
        launch_simple_mailing(
            draft=_launch_draft(
                count=1,
                template_id=template.pk,
                recipient_scope=RECIPIENT_SCOPE_CONTROL_ONLY,
            ),
            template=template,
            created_by=self.user,
            launch_key=str(uuid.uuid4()),
        )
        with self.assertRaises(SimpleMailingLaunchError):
            launch_simple_mailing(
                draft=_launch_draft(
                    count=3,
                    template_id=template.pk,
                    recipient_scope=RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
                ),
                template=template,
                created_by=self.user,
                launch_key=str(uuid.uuid4()),
            )


class ControlSendTimeRecheckTests(TestCase):
    def test_control_test_contact_allowed_on_recheck(self):
        recipient = MarketingCampaignRecipient(
            phone_normalized=make_buyer(is_test_contact=True).phone_normalized,
            is_test_contact=True,
            is_control_recipient=True,
        )
        eligible, reason = recheck_simple_mailing_recipient(recipient)
        self.assertTrue(eligible)
        self.assertEqual(reason, '')


class ControlTestSellerRecheckTests(TestCase):
    def _test_seller_phone(self) -> str:
        phone = next_phone()
        Seller.objects.create(
            name='Test seller control',
            whatsapp=phone,
            transport_type='car',
            city='Алматы',
            is_active=True,
            is_test_seller=True,
        )
        return phone

    def _recipient(self, *, phone: str, is_control: bool, is_test: bool) -> MarketingCampaignRecipient:
        return MarketingCampaignRecipient(
            phone_normalized=phone,
            is_test_contact=is_test,
            is_control_recipient=is_control,
        )

    def test_control_test_seller_phone_allowed(self):
        phone = self._test_seller_phone()
        make_buyer(
            phone_normalized=phone,
            is_test_contact=True,
            status=BUYER_CONTACT_STATUS_ACTIVE,
        )
        grant_consent(
            BuyerContact.objects.get(phone_normalized=phone),
            CONTACT_CONSENT_STATUS_GRANTED,
        )
        eligible, reason = recheck_simple_mailing_recipient(
            self._recipient(phone=phone, is_control=True, is_test=True),
        )
        self.assertTrue(eligible)
        self.assertEqual(reason, '')

    def test_non_control_test_seller_phone_skipped(self):
        phone = self._test_seller_phone()
        make_buyer(phone_normalized=phone, is_test_contact=False)
        eligible, reason = recheck_simple_mailing_recipient(
            self._recipient(phone=phone, is_control=False, is_test=False),
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, SKIP_REASON_TEST_CONTACT)

    def test_control_test_seller_revoked_consent_blocked(self):
        from core.models import CONTACT_CONSENT_STATUS_REVOKED

        phone = self._test_seller_phone()
        buyer = make_buyer(
            phone_normalized=phone,
            is_test_contact=True,
            status=BUYER_CONTACT_STATUS_ACTIVE,
        )
        grant_consent(buyer, CONTACT_CONSENT_STATUS_REVOKED)
        eligible, reason = recheck_simple_mailing_recipient(
            self._recipient(phone=phone, is_control=True, is_test=True),
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, SKIP_REASON_CONSENT_REVOKED)

    def test_control_test_seller_unsubscribed_blocked(self):
        phone = self._test_seller_phone()
        make_buyer(
            phone_normalized=phone,
            is_test_contact=True,
            status=BUYER_CONTACT_STATUS_UNSUBSCRIBED,
        )
        eligible, reason = recheck_simple_mailing_recipient(
            self._recipient(phone=phone, is_control=True, is_test=True),
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, SKIP_REASON_UNSUBSCRIBED)

    def test_control_test_seller_blocked_status_blocked(self):
        phone = self._test_seller_phone()
        make_buyer(
            phone_normalized=phone,
            is_test_contact=True,
            status=BUYER_CONTACT_STATUS_BLOCKED,
        )
        eligible, reason = recheck_simple_mailing_recipient(
            self._recipient(phone=phone, is_control=True, is_test=True),
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, SKIP_REASON_BLOCKED)

    def test_legacy_recheck_phone_unchanged_for_ordinary_test_contact(self):
        buyer = make_buyer(is_test_contact=True)
        eligible, reason = recheck_simple_mailing_phone(phone_normalized=buyer.phone_normalized)
        self.assertFalse(eligible)
        self.assertEqual(reason, SKIP_REASON_TEST_CONTACT)


class ControlHistoryImmutabilityTests(TestCase):
    @override_settings(**LIVE_SIMPLE_SETTINGS)
    def test_existing_terminal_run_not_mutated(self):
        user = User.objects.create_user('legacy-run', password='secret', is_staff=True)
        template = make_test_send_template(user)
        campaign = MarketingCampaign.objects.create(
            name='Legacy run #3',
            audience=make_audience(name='Legacy audience', created_by=user),
            purpose=PURPOSE_PARTS_BUYERS,
            channel=CHANNEL_WHATSAPP,
            status=STATUS_AUDIENCE_PREPARED,
            message_template=template,
            created_by=user,
        )
        recipient = MarketingCampaignRecipient.objects.create(
            campaign=campaign,
            phone_normalized='77001112233',
            display_name='Legacy',
            city='Алматы',
            roles=['Покупатель'],
            vehicle_summary='Toyota',
            is_test_contact=False,
            is_control_recipient=False,
            consent_status='granted',
            eligibility_status=ELIGIBILITY_ELIGIBLE,
        )
        send_run = MarketingCampaignSendRun.objects.create(
            campaign=campaign,
            template=template,
            status=SEND_RUN_STATUS_PARTIAL,
            workflow_type=WORKFLOW_TYPE_SIMPLE_MAILING,
            total_count=12,
            queued_count=0,
            sent_count=2,
            failed_count=10,
            skipped_count=0,
            recipient_scope='',
        )
        message = MarketingCampaignMessage.objects.create(
            send_run=send_run,
            campaign_recipient=recipient,
            phone_normalized=recipient.phone_normalized,
            template_name=template.meta_template_name,
            language_code=template.language_code,
            variables={'request_history_url': 'https://zpt.kz/my-requests/token/'},
            status=MESSAGE_STATUS_QUEUED,
            wave_number=1,
            position_number=1,
            scheduled_at=timezone.now(),
        )
        snapshot_before = {
            'run_status': send_run.status,
            'run_total': send_run.total_count,
            'run_sent': send_run.sent_count,
            'run_failed': send_run.failed_count,
            'recipient_control': recipient.is_control_recipient,
            'message_status': message.status,
        }
        _make_control_buyer()
        launch_simple_mailing(
            draft=_launch_draft(
                count=1,
                template_id=template.pk,
                recipient_scope=RECIPIENT_SCOPE_CONTROL_ONLY,
            ),
            template=template,
            created_by=user,
            launch_key=str(uuid.uuid4()),
        )
        send_run.refresh_from_db()
        recipient.refresh_from_db()
        message.refresh_from_db()
        self.assertEqual(send_run.status, snapshot_before['run_status'])
        self.assertEqual(send_run.total_count, snapshot_before['run_total'])
        self.assertEqual(send_run.sent_count, snapshot_before['run_sent'])
        self.assertEqual(send_run.failed_count, snapshot_before['run_failed'])
        self.assertEqual(recipient.is_control_recipient, snapshot_before['recipient_control'])
        self.assertEqual(message.status, snapshot_before['message_status'])


class ControlUiDefaultsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('control-ui', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='control-ui', password='secret')
        self.url = reverse('marketing:new_mailing')

    def test_default_recipient_scope_is_control_only(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'name="recipient_scope"')
        self.assertContains(response, 'value="control_only"')
        self.assertEqual(response.context['recipient_scope'], RECIPIENT_SCOPE_CONTROL_ONLY)
        self.assertEqual(DEFAULT_RECIPIENT_SCOPE, RECIPIENT_SCOPE_CONTROL_ONLY)

    def test_control_only_zero_controls_blocked_in_ui(self):
        make_request(make_buyer(), brand='Toyota')
        self.client.post(
            self.url,
            {
                'action': 'preview',
                'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                'recipient_scope': RECIPIENT_SCOPE_CONTROL_ONLY,
                'all_brands': '1',
            },
        )
        response = self.client.post(
            self.url,
            {
                'action': 'continue',
                'recipient_type': RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
                'recipient_scope': RECIPIENT_SCOPE_CONTROL_ONLY,
                'all_brands': '1',
            },
        )
        self.assertContains(response, 'Контрольные получатели не найдены')
