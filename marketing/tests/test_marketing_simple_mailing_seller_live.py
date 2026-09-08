from __future__ import annotations

import uuid
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import (
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    Seller,
    SellerContactConsent,
)
from marketing.models import MarketingCampaignMessage, MarketingCampaignRecipient, MarketingCampaignSendRun
from marketing.services.campaigns.constants import PURPOSE_REQUEST_SELLERS
from marketing.services.campaigns.live_consent import (
    SKIP_REASON_CONSENT_NOT_GRANTED,
    SKIP_REASON_CONSENT_REVOKED,
    SKIP_REASON_SELLER_NOT_RECEIVING,
    recheck_live_recipient_consent,
)
from marketing.services.campaigns.live_processor import process_marketing_live_send_batch
from marketing.services.campaigns.send_constants import (
    MESSAGE_STATUS_QUEUED,
    MESSAGE_STATUS_SENT,
    MESSAGE_STATUS_SKIPPED,
    RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
)
from marketing.services.simple_mailing.consent import recheck_simple_mailing_recipient
from marketing.services.simple_mailing.constants import RECIPIENT_TYPE_SELLERS
from marketing.services.simple_mailing.launch import (
    SimpleMailingCountChangedError,
    SimpleMailingLaunchError,
    launch_simple_mailing,
)
from marketing.services.simple_mailing.launch_recipients import resolve_simple_mailing_launch_recipients
from marketing.tests.test_marketing_audiences import next_phone
from marketing.tests.test_marketing_simple_mailing_live import (
    LIVE_SIMPLE_SETTINGS,
    _launch_draft,
    _make_n_request_buyers,
    _make_parts_buyer_template,
    _mock_send_ok,
)
from marketing.tests.test_marketing_templates import make_template
from marketing.tests.test_seller_marketing_consent import grant_seller_consent


def _make_seller_template(user: User):
    return make_template(
        user,
        name='Seller simple mailing',
        allowed_purposes=[PURPOSE_REQUEST_SELLERS],
        variables=[],
    )


def _make_seller(**kwargs) -> Seller:
    defaults = {
        'name': 'Seller',
        'whatsapp': next_phone(),
        'transport_type': 'car',
        'city': 'Алматы',
        'is_active': True,
        'is_test_seller': False,
        'is_paused': False,
        'receive_requests': True,
        'brand': 'Toyota',
    }
    defaults.update(kwargs)
    return Seller.objects.create(**defaults)


def _seller_draft(*, seller_ids: list[int], template_id: int) -> dict:
    rows = resolve_simple_mailing_launch_recipients(
        recipient_type=RECIPIENT_TYPE_SELLERS,
        recipient_scope=RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
        all_brands=False,
        brands=['Toyota'],
        selected_seller_ids=seller_ids,
    )
    return {
        'recipient_type': RECIPIENT_TYPE_SELLERS,
        'recipient_scope': RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
        'all_brands': False,
        'brands': ['Toyota'],
        'count': len(rows),
        'selected_seller_ids': seller_ids,
        'template_id': template_id,
    }


def _sent_phones(mocked) -> set[str]:
    return {call.args[0] for call in mocked.call_args_list}


@override_settings(**LIVE_SIMPLE_SETTINGS)
class SimpleMailingSellerLiveSendPathTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('seller-live', password='secret', is_staff=True)
        self.template = _make_seller_template(self.user)

    def _launch(self, seller_ids: list[int]):
        return launch_simple_mailing(
            draft=_seller_draft(seller_ids=seller_ids, template_id=self.template.pk),
            template=self.template,
            created_by=self.user,
            launch_key=str(uuid.uuid4()),
        )

    def _process(self, mocked):
        return process_marketing_live_send_batch(send_callable=mocked, interval_seconds=0)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_consent_not_recorded_seller_never_reaches_send_callable(self, mocked):
        mocked.side_effect = _mock_send_ok
        seller = _make_seller(name='No consent')
        with self.assertRaises(SimpleMailingLaunchError):
            self._launch([seller.pk])
        self.assertEqual(MarketingCampaignMessage.objects.count(), 0)
        self._process(mocked)
        mocked.assert_not_called()

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_revoked_seller_never_reaches_send_callable(self, mocked):
        mocked.side_effect = _mock_send_ok
        seller = _make_seller(name='Revoked')
        grant_seller_consent(seller, CONTACT_CONSENT_STATUS_REVOKED)
        with self.assertRaises(SimpleMailingLaunchError):
            self._launch([seller.pk])
        self.assertEqual(MarketingCampaignMessage.objects.count(), 0)
        self._process(mocked)
        mocked.assert_not_called()

    @patch('marketing.services.campaigns.live_processor.recheck_simple_mailing_recipient', wraps=recheck_simple_mailing_recipient)
    @patch('marketing.services.campaigns.live_processor.recheck_live_recipient_consent', wraps=recheck_live_recipient_consent)
    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_granted_operational_seller_is_eligible_and_sent(
        self,
        mocked,
        live_recheck,
        buyer_recheck,
    ):
        mocked.side_effect = _mock_send_ok
        seller = _make_seller(name='Granted')
        grant_seller_consent(seller, CONTACT_CONSENT_STATUS_GRANTED)
        result = self._launch([seller.pk])
        recipient = MarketingCampaignRecipient.objects.get(
            campaign_id=result.campaign_id,
            phone_normalized=seller.whatsapp,
        )
        self.assertEqual(recipient.consent_status, CONTACT_CONSENT_STATUS_GRANTED)
        send_run = MarketingCampaignSendRun.objects.get(pk=result.send_run_id)
        self.assertEqual(send_run.messages.filter(status=MESSAGE_STATUS_QUEUED).count(), 1)
        batch = self._process(mocked)
        self.assertEqual(batch.sent_count, 1)
        self.assertEqual(_sent_phones(mocked), {seller.whatsapp})
        live_recheck.assert_called()
        buyer_recheck.assert_not_called()
        message = send_run.messages.get()
        self.assertEqual(message.status, MESSAGE_STATUS_SENT)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_mixed_selected_sellers_only_granted_is_queued_and_sent(self, mocked):
        mocked.side_effect = _mock_send_ok
        granted = _make_seller(name='Granted')
        grant_seller_consent(granted, CONTACT_CONSENT_STATUS_GRANTED)
        missing = [_make_seller(name=f'Missing {index}') for index in range(3)]
        revoked = _make_seller(name='Revoked')
        grant_seller_consent(revoked, CONTACT_CONSENT_STATUS_REVOKED)
        extra_same_brand = _make_seller(name='Not selected')
        grant_seller_consent(extra_same_brand, CONTACT_CONSENT_STATUS_GRANTED)
        selected_ids = [granted.pk, *[seller.pk for seller in missing], revoked.pk]
        result = self._launch(selected_ids)
        send_run = MarketingCampaignSendRun.objects.get(pk=result.send_run_id)
        self.assertEqual(result.queued_count, 1)
        self.assertEqual(result.skipped_count, 4)
        queued = send_run.messages.filter(status=MESSAGE_STATUS_QUEUED)
        self.assertEqual(queued.count(), 1)
        self.assertEqual(queued.get().phone_normalized, granted.whatsapp)
        skipped_phones = set(
            send_run.messages.filter(status=MESSAGE_STATUS_SKIPPED).values_list(
                'phone_normalized',
                flat=True,
            )
        )
        self.assertEqual(
            skipped_phones,
            {seller.whatsapp for seller in missing} | {revoked.whatsapp},
        )
        self.assertFalse(
            send_run.messages.filter(phone_normalized=extra_same_brand.whatsapp).exists(),
        )
        skipped_reasons = set(
            send_run.messages.filter(status=MESSAGE_STATUS_SKIPPED).values_list(
                'error_code',
                flat=True,
            )
        )
        self.assertEqual(
            skipped_reasons,
            {SKIP_REASON_CONSENT_NOT_GRANTED, SKIP_REASON_CONSENT_REVOKED},
        )
        self._process(mocked)
        self.assertEqual(_sent_phones(mocked), {granted.whatsapp})

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_revoked_after_launch_is_skipped_before_send(self, mocked):
        mocked.side_effect = _mock_send_ok
        seller = _make_seller(name='Granted then revoked')
        grant_seller_consent(seller, CONTACT_CONSENT_STATUS_GRANTED)
        result = self._launch([seller.pk])
        SellerContactConsent.objects.filter(seller=seller).update(
            status=CONTACT_CONSENT_STATUS_REVOKED,
            revoked_at=timezone.now(),
        )
        send_run = MarketingCampaignSendRun.objects.get(pk=result.send_run_id)
        self.assertEqual(send_run.messages.filter(status=MESSAGE_STATUS_QUEUED).count(), 1)
        batch = self._process(mocked)
        self.assertEqual(batch.skipped_count, 1)
        mocked.assert_not_called()
        message = send_run.messages.get()
        self.assertEqual(message.status, MESSAGE_STATUS_SKIPPED)
        self.assertEqual(message.error_code, SKIP_REASON_CONSENT_REVOKED)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_paused_after_launch_is_skipped_before_send(self, mocked):
        mocked.side_effect = _mock_send_ok
        seller = _make_seller(name='Granted then paused')
        grant_seller_consent(seller, CONTACT_CONSENT_STATUS_GRANTED)
        result = self._launch([seller.pk])
        seller.is_paused = True
        seller.save(update_fields=['is_paused'])
        batch = self._process(mocked)
        self.assertEqual(batch.skipped_count, 1)
        mocked.assert_not_called()
        message = MarketingCampaignSendRun.objects.get(pk=result.send_run_id).messages.get()
        self.assertEqual(message.status, MESSAGE_STATUS_SKIPPED)
        self.assertEqual(message.error_code, SKIP_REASON_SELLER_NOT_RECEIVING)

    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_not_receiving_after_launch_is_skipped_before_send(self, mocked):
        mocked.side_effect = _mock_send_ok
        seller = _make_seller(name='Granted then closed')
        grant_seller_consent(seller, CONTACT_CONSENT_STATUS_GRANTED)
        result = self._launch([seller.pk])
        seller.receive_requests = False
        seller.save(update_fields=['receive_requests'])
        batch = self._process(mocked)
        self.assertEqual(batch.skipped_count, 1)
        mocked.assert_not_called()
        message = MarketingCampaignSendRun.objects.get(pk=result.send_run_id).messages.get()
        self.assertEqual(message.status, MESSAGE_STATUS_SKIPPED)
        self.assertEqual(message.error_code, SKIP_REASON_SELLER_NOT_RECEIVING)

    def test_paused_selected_seller_is_not_replaced_by_another(self):
        first = _make_seller(name='Keep')
        second = _make_seller(name='Will pause')
        extra = _make_seller(name='Same brand extra')
        grant_seller_consent(first, CONTACT_CONSENT_STATUS_GRANTED)
        grant_seller_consent(second, CONTACT_CONSENT_STATUS_GRANTED)
        grant_seller_consent(extra, CONTACT_CONSENT_STATUS_GRANTED)
        draft = _seller_draft(
            seller_ids=[first.pk, second.pk],
            template_id=self.template.pk,
        )
        self.assertEqual(draft['count'], 2)
        second.is_paused = True
        second.save(update_fields=['is_paused'])
        with self.assertRaises(SimpleMailingCountChangedError):
            launch_simple_mailing(
                draft=draft,
                template=self.template,
                created_by=self.user,
                launch_key=str(uuid.uuid4()),
            )
        self.assertEqual(MarketingCampaignMessage.objects.count(), 0)


@override_settings(**LIVE_SIMPLE_SETTINGS)
class SimpleMailingBuyerLiveRegressionTests(TestCase):
    @patch('marketing.services.campaigns.live_processor.recheck_simple_mailing_recipient', wraps=recheck_simple_mailing_recipient)
    @patch('marketing.services.campaigns.live_processor.recheck_live_recipient_consent', wraps=recheck_live_recipient_consent)
    @patch('marketing.services.campaigns.live_processor.send_whatsapp_template_message')
    def test_buyer_simple_mailing_still_uses_buyer_recheck(self, mocked, live_recheck, buyer_recheck):
        mocked.side_effect = _mock_send_ok
        user = User.objects.create_user('buyer-live-reg', password='secret', is_staff=True)
        _make_n_request_buyers(1, consent_status=CONTACT_CONSENT_STATUS_UNKNOWN)
        template = _make_parts_buyer_template(user)
        result = launch_simple_mailing(
            draft=_launch_draft(count=1, template_id=template.pk),
            template=template,
            created_by=user,
            launch_key=str(uuid.uuid4()),
        )
        send_run = MarketingCampaignSendRun.objects.get(pk=result.send_run_id)
        self.assertEqual(result.queued_count, 1)
        self.assertEqual(send_run.messages.filter(status=MESSAGE_STATUS_QUEUED).count(), 1)
        process_marketing_live_send_batch(send_callable=mocked, interval_seconds=0)
        self.assertEqual(send_run.messages.get().status, MESSAGE_STATUS_SENT)
        buyer_recheck.assert_called()
        live_recheck.assert_not_called()
        mocked.assert_called_once()
