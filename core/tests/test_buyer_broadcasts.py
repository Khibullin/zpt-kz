from __future__ import annotations

import os
from io import StringIO
from unittest.mock import patch

from django.contrib.admin.sites import site as default_admin_site
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.buyer_broadcast_admin_forms import BuyerBroadcastCampaignAdminForm
from core.models import (
    BUYER_BROADCAST_RECIPIENT_QUEUED,
    BUYER_BROADCAST_RECIPIENT_SENT,
    BUYER_BROADCAST_RECIPIENT_SKIPPED,
    BUYER_BROADCAST_STATUS_COMPLETED,
    BUYER_BROADCAST_STATUS_DRAFT,
    BUYER_BROADCAST_STATUS_QUEUED,
    BUYER_CONTACT_STATUS_ACTIVE,
    BUYER_CONTACT_STATUS_BLOCKED,
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    BuyerBroadcastCampaign,
    BuyerBroadcastRecipient,
    BuyerContact,
    ContactConsent,
)
from core.services.buyer_broadcast_service import (
    SKIP_MARKETING_MISSING,
    SKIP_NOT_TEST_CONTACT,
    prepare_test_campaign,
    preview_test_campaign,
    process_buyer_broadcast_campaign,
    send_buyer_broadcast_recipient,
)
from core.services.buyer_broadcast_settings import (
    buyer_test_broadcast_enabled,
    get_buyer_broadcast_mode,
    get_buyer_broadcast_test_max_recipients,
)

_phone_counter = 9100000


def make_buyer(**kwargs) -> BuyerContact:
    global _phone_counter
    _phone_counter += 1
    defaults = {
        'phone_normalized': f'77{_phone_counter:09d}'[-11:],
        'status': BUYER_CONTACT_STATUS_ACTIVE,
    }
    defaults.update(kwargs)
    return BuyerContact.objects.create(**defaults)


def make_campaign(**kwargs) -> BuyerBroadcastCampaign:
    defaults = {
        'name': 'Test campaign',
        'template_name': 'marketing_test_template',
        'template_language': 'ru',
        'template_body_parameters': ['hello'],
        'message_preview': 'Preview only',
        'status': BUYER_BROADCAST_STATUS_DRAFT,
    }
    defaults.update(kwargs)
    return BuyerBroadcastCampaign.objects.create(**defaults)


def grant_marketing(buyer: BuyerContact) -> None:
    ContactConsent.objects.create(
        buyer=buyer,
        channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
        purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        status=CONTACT_CONSENT_STATUS_GRANTED,
        consented_at=timezone.now(),
    )


@override_settings(BUYER_BROADCAST_MODE='OFF')
class BuyerBroadcastSettingsTests(TestCase):
    def test_missing_mode_defaults_to_off(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_buyer_broadcast_mode(), 'OFF')

    def test_unknown_mode_defaults_to_off(self):
        with patch.dict(os.environ, {'BUYER_BROADCAST_MODE': 'LIVE'}, clear=True):
            self.assertEqual(get_buyer_broadcast_mode(), 'OFF')

    @override_settings(BUYER_BROADCAST_MODE='test')
    def test_test_mode_recognized_case_insensitive(self):
        with patch.dict(os.environ, {'BUYER_BROADCAST_MODE': 'test'}, clear=True):
            self.assertTrue(buyer_test_broadcast_enabled())

    def test_invalid_max_recipients_defaults_to_five(self):
        with patch.dict(os.environ, {'BUYER_BROADCAST_TEST_MAX_RECIPIENTS': 'bad'}, clear=True):
            self.assertEqual(get_buyer_broadcast_test_max_recipients(), 5)


@override_settings(BUYER_BROADCAST_MODE='TEST', BUYER_BROADCAST_TEST_MAX_RECIPIENTS=5)
class BuyerBroadcastFormTests(TestCase):
    def setUp(self):
        self.test_buyer = make_buyer(is_test_contact=True, primary_city='Алматы')
        self.regular_buyer = make_buyer(is_test_contact=False, primary_city='Астана')

    def test_test_contacts_queryset_only_test_contacts(self):
        form = BuyerBroadcastCampaignAdminForm()
        ids = set(form.fields['test_contacts'].queryset.values_list('pk', flat=True))
        self.assertIn(self.test_buyer.pk, ids)
        self.assertNotIn(self.regular_buyer.pk, ids)

    def test_regular_contact_cannot_be_saved(self):
        campaign = make_campaign()
        form = BuyerBroadcastCampaignAdminForm(
            data={
                'name': campaign.name,
                'description': '',
                'mode': 'test',
                'status': 'draft',
                'template_name': 'tpl',
                'template_language': 'ru',
                'template_body_parameters': '[]',
                'message_preview': '',
                'test_contacts': [self.regular_buyer.pk],
            },
            instance=campaign,
        )
        form.fields['test_contacts'].queryset = BuyerContact.objects.filter(
            pk=self.regular_buyer.pk,
        )
        self.assertFalse(form.is_valid())

    def test_recipient_limit_validated(self):
        buyers = [make_buyer(is_test_contact=True) for _ in range(6)]
        campaign = make_campaign()
        form = BuyerBroadcastCampaignAdminForm(
            data={
                'name': campaign.name,
                'description': '',
                'mode': 'test',
                'status': 'draft',
                'template_name': 'tpl',
                'template_language': 'ru',
                'template_body_parameters': '[]',
                'message_preview': '',
                'test_contacts': [buyer.pk for buyer in buyers],
            },
            instance=campaign,
        )
        self.assertFalse(form.is_valid())

    def test_label_does_not_contain_full_phone(self):
        form = BuyerBroadcastCampaignAdminForm()
        label = form._contact_label(self.test_buyer)
        self.assertIn('***', label)
        self.assertNotIn(self.test_buyer.phone_normalized, label)


@override_settings(BUYER_BROADCAST_MODE='TEST', BUYER_BROADCAST_TEST_MAX_RECIPIENTS=5)
class BuyerBroadcastPreviewTests(TestCase):
    def setUp(self):
        self.campaign = make_campaign()
        self.granted = make_buyer(is_test_contact=True)
        self.unknown = make_buyer(is_test_contact=True)
        self.revoked = make_buyer(is_test_contact=True)
        self.missing = make_buyer(is_test_contact=True)
        self.blocked = make_buyer(
            is_test_contact=True,
            status=BUYER_CONTACT_STATUS_BLOCKED,
        )
        self.regular = make_buyer(is_test_contact=False)
        self.bad_phone = make_buyer(
            is_test_contact=True,
            phone_normalized='123',
        )
        grant_marketing(self.granted)
        ContactConsent.objects.create(
            buyer=self.unknown,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_UNKNOWN,
        )
        ContactConsent.objects.create(
            buyer=self.revoked,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_REVOKED,
            revoked_at=timezone.now(),
        )
        self.campaign.test_contacts.set([
            self.granted,
            self.unknown,
            self.revoked,
            self.missing,
            self.blocked,
            self.regular,
            self.bad_phone,
        ])

    @override_settings(BUYER_BROADCAST_MODE='OFF')
    def test_off_mode_blocks_prepare_errors(self):
        result = preview_test_campaign(self.campaign)
        self.assertIn('BUYER_BROADCAST_MODE должен быть TEST.', result.errors)

    def test_test_mode_allows_preview(self):
        result = preview_test_campaign(self.campaign)
        self.assertEqual(result.selected_count, 7)

    def test_non_test_contact_excluded(self):
        result = preview_test_campaign(self.campaign)
        regular = next(
            contact for contact in result.contacts if contact.buyer_id == self.regular.pk
        )
        self.assertFalse(regular.eligible)
        self.assertEqual(regular.skip_reason, SKIP_NOT_TEST_CONTACT)

    def test_blocked_test_contact_excluded(self):
        result = preview_test_campaign(self.campaign)
        blocked = next(
            contact for contact in result.contacts if contact.buyer_id == self.blocked.pk
        )
        self.assertFalse(blocked.eligible)

    def test_marketing_unknown_excluded(self):
        result = preview_test_campaign(self.campaign)
        unknown = next(
            contact for contact in result.contacts if contact.buyer_id == self.unknown.pk
        )
        self.assertFalse(unknown.eligible)

    def test_marketing_revoked_excluded(self):
        result = preview_test_campaign(self.campaign)
        revoked = next(
            contact for contact in result.contacts if contact.buyer_id == self.revoked.pk
        )
        self.assertFalse(revoked.eligible)

    def test_marketing_missing_excluded(self):
        result = preview_test_campaign(self.campaign)
        missing = next(
            contact for contact in result.contacts if contact.buyer_id == self.missing.pk
        )
        self.assertFalse(missing.eligible)
        self.assertEqual(missing.skip_reason, SKIP_MARKETING_MISSING)

    def test_marketing_granted_allowed(self):
        result = preview_test_campaign(self.campaign)
        granted = next(
            contact for contact in result.contacts if contact.buyer_id == self.granted.pk
        )
        self.assertTrue(granted.eligible)

    def test_invalid_phone_excluded(self):
        result = preview_test_campaign(self.campaign)
        bad = next(
            contact for contact in result.contacts if contact.buyer_id == self.bad_phone.pk
        )
        self.assertFalse(bad.eligible)

    def test_preview_creates_nothing(self):
        before = BuyerBroadcastRecipient.objects.count()
        preview_test_campaign(self.campaign)
        self.assertEqual(BuyerBroadcastRecipient.objects.count(), before)

    @patch('core.services.buyer_broadcast_service.send_whatsapp_template_message')
    def test_preview_does_not_call_meta(self, mocked_sender):
        preview_test_campaign(self.campaign)
        mocked_sender.assert_not_called()


@override_settings(BUYER_BROADCAST_MODE='TEST', BUYER_BROADCAST_TEST_MAX_RECIPIENTS=5)
class BuyerBroadcastPrepareTests(TestCase):
    def setUp(self):
        self.campaign = make_campaign()
        self.granted = make_buyer(is_test_contact=True)
        self.missing = make_buyer(is_test_contact=True)
        grant_marketing(self.granted)
        self.campaign.test_contacts.set([self.granted, self.missing])

    def test_prepare_creates_queued_recipient_for_granted(self):
        result = prepare_test_campaign(self.campaign)
        self.assertFalse(result.errors)
        recipient = BuyerBroadcastRecipient.objects.get(
            campaign=self.campaign,
            buyer=self.granted,
        )
        self.assertEqual(recipient.status, BUYER_BROADCAST_RECIPIENT_QUEUED)

    def test_prepare_creates_skipped_recipient_with_reason(self):
        prepare_test_campaign(self.campaign)
        recipient = BuyerBroadcastRecipient.objects.get(
            campaign=self.campaign,
            buyer=self.missing,
        )
        self.assertEqual(recipient.status, BUYER_BROADCAST_RECIPIENT_SKIPPED)
        self.assertEqual(recipient.skip_reason, SKIP_MARKETING_MISSING)

    def test_repeat_prepare_does_not_duplicate(self):
        prepare_test_campaign(self.campaign)
        prepare_test_campaign(self.campaign)
        self.assertEqual(self.campaign.recipients.count(), 2)

    def test_sent_recipient_not_reset_to_queued(self):
        prepare_test_campaign(self.campaign)
        recipient = BuyerBroadcastRecipient.objects.get(
            campaign=self.campaign,
            buyer=self.granted,
        )
        recipient.status = BUYER_BROADCAST_RECIPIENT_SENT
        recipient.save(update_fields=['status'])
        prepare_test_campaign(self.campaign)
        recipient.refresh_from_db()
        self.assertEqual(recipient.status, BUYER_BROADCAST_RECIPIENT_SENT)

    def test_zero_eligible_does_not_queue_campaign(self):
        campaign = make_campaign()
        buyer = make_buyer(is_test_contact=True)
        campaign.test_contacts.set([buyer])
        result = prepare_test_campaign(campaign)
        campaign.refresh_from_db()
        self.assertTrue(result.errors)
        self.assertNotEqual(campaign.status, BUYER_BROADCAST_STATUS_QUEUED)

    @override_settings(BUYER_BROADCAST_TEST_MAX_RECIPIENTS=1)
    def test_limit_blocks_prepare(self):
        second = make_buyer(is_test_contact=True)
        grant_marketing(second)
        self.campaign.test_contacts.add(second)
        result = prepare_test_campaign(self.campaign)
        self.assertTrue(result.errors)


@override_settings(BUYER_BROADCAST_MODE='TEST', BUYER_BROADCAST_TEST_MAX_RECIPIENTS=5)
class BuyerBroadcastSendTests(TestCase):
    def setUp(self):
        self.campaign = make_campaign(status=BUYER_BROADCAST_STATUS_QUEUED)
        self.granted = make_buyer(is_test_contact=True)
        self.second = make_buyer(is_test_contact=True)
        grant_marketing(self.granted)
        grant_marketing(self.second)
        self.campaign.test_contacts.set([self.granted, self.second])
        prepare_test_campaign(self.campaign)
        self.recipient = BuyerBroadcastRecipient.objects.get(
            campaign=self.campaign,
            buyer=self.granted,
        )

    @patch('core.services.buyer_broadcast_service.send_whatsapp_template_message')
    def test_rechecks_test_flag_before_send(self, mocked_sender):
        self.granted.is_test_contact = False
        self.granted.save(update_fields=['is_test_contact'])
        result = send_buyer_broadcast_recipient(self.recipient)
        self.assertTrue(result.skipped)
        mocked_sender.assert_not_called()

    @patch('core.services.buyer_broadcast_service.send_whatsapp_template_message')
    def test_rechecks_consent_before_send(self, mocked_sender):
        ContactConsent.objects.filter(buyer=self.granted).update(
            status=CONTACT_CONSENT_STATUS_REVOKED,
        )
        result = send_buyer_broadcast_recipient(self.recipient)
        self.assertTrue(result.skipped)
        mocked_sender.assert_not_called()

    @override_settings(BUYER_BROADCAST_MODE='OFF')
    @patch('core.services.buyer_broadcast_service.send_whatsapp_template_message')
    def test_off_mode_blocks_sender(self, mocked_sender):
        result = send_buyer_broadcast_recipient(self.recipient)
        self.assertTrue(result.skipped)
        mocked_sender.assert_not_called()

    @patch('core.services.buyer_broadcast_service.send_whatsapp_template_message')
    def test_non_test_never_calls_sender(self, mocked_sender):
        regular = make_buyer(is_test_contact=False)
        grant_marketing(regular)
        recipient = BuyerBroadcastRecipient.objects.create(
            campaign=self.campaign,
            buyer=regular,
            phone_snapshot=regular.phone_normalized,
            masked_phone_snapshot='7701***0000',
            status=BUYER_BROADCAST_RECIPIENT_QUEUED,
            queued_at=timezone.now(),
        )
        result = send_buyer_broadcast_recipient(recipient)
        self.assertTrue(result.skipped)
        mocked_sender.assert_not_called()

    @patch(
        'core.services.buyer_broadcast_service.send_whatsapp_template_message',
        return_value={'ok': True, 'message_id': 'wamid.TEST123', 'status_code': 200},
    )
    def test_success_stores_provider_message_id(self, mocked_sender):
        result = send_buyer_broadcast_recipient(self.recipient)
        self.recipient.refresh_from_db()
        self.assertTrue(result.ok)
        self.assertEqual(self.recipient.provider_message_id, 'wamid.TEST123')
        mocked_sender.assert_called_once()
        self.assertEqual(
            mocked_sender.call_args.kwargs['template_name'],
            self.campaign.template_name,
        )

    @patch(
        'core.services.buyer_broadcast_service.send_whatsapp_template_message',
        return_value={'ok': False, 'error': 'HTTP 400', 'status_code': 400},
    )
    def test_failure_saved_as_failed(self, mocked_sender):
        result = send_buyer_broadcast_recipient(self.recipient)
        self.recipient.refresh_from_db()
        self.assertFalse(result.ok)
        self.assertEqual(self.recipient.status, 'failed')

    @patch(
        'core.services.buyer_broadcast_service.send_whatsapp_template_message',
        side_effect=[
            {'ok': False, 'error': 'HTTP 400', 'status_code': 400},
            {'ok': True, 'message_id': 'wamid.OK', 'status_code': 200},
        ],
    )
    def test_one_failure_does_not_stop_second(self, mocked_sender):
        second = BuyerBroadcastRecipient.objects.get(
            campaign=self.campaign,
            buyer=self.second,
        )
        send_buyer_broadcast_recipient(self.recipient)
        result = send_buyer_broadcast_recipient(second)
        self.assertTrue(result.ok)

    @patch(
        'core.services.buyer_broadcast_service.send_whatsapp_template_message',
        return_value={'ok': True, 'message_id': 'wamid.TEST123', 'status_code': 200},
    )
    def test_completed_campaign_not_sent_again(self, mocked_sender):
        self.campaign.status = BUYER_BROADCAST_STATUS_COMPLETED
        self.campaign.save(update_fields=['status'])
        result = process_buyer_broadcast_campaign(self.campaign)
        self.assertTrue(result.errors)
        mocked_sender.assert_not_called()

    @patch(
        'core.services.buyer_broadcast_service.send_whatsapp_template_message',
        return_value={'ok': True, 'message_id': 'wamid.TEST123', 'status_code': 200},
    )
    def test_message_preview_not_sent_as_text(self, mocked_sender):
        send_buyer_broadcast_recipient(self.recipient)
        self.assertNotIn('message_preview', str(mocked_sender.call_args))

    @patch(
        'core.services.buyer_broadcast_service.send_whatsapp_template_message',
        return_value={'ok': True, 'message_id': 'wamid.TEST123', 'status_code': 200},
    )
    def test_uses_campaign_template_name(self, mocked_sender):
        send_buyer_broadcast_recipient(self.recipient)
        self.assertEqual(
            mocked_sender.call_args.kwargs['template_name'],
            'marketing_test_template',
        )


@override_settings(BUYER_BROADCAST_MODE='TEST', BUYER_BROADCAST_TEST_MAX_RECIPIENTS=5)
class BuyerBroadcastCommandTests(TestCase):
    def setUp(self):
        self.campaign = make_campaign()
        self.buyer = make_buyer(is_test_contact=True)
        grant_marketing(self.buyer)
        self.campaign.test_contacts.set([self.buyer])

    def test_default_dry_run(self):
        out = StringIO()
        call_command('process_buyer_broadcasts', '--campaign-id', self.campaign.pk, stdout=out)
        output = out.getvalue()
        self.assertIn('dry-run', output)
        self.assertEqual(BuyerBroadcastRecipient.objects.count(), 0)

    def test_dry_run_does_not_create_recipients(self):
        call_command('process_buyer_broadcasts', '--campaign-id', self.campaign.pk)
        self.assertEqual(BuyerBroadcastRecipient.objects.count(), 0)

    @patch('core.services.buyer_broadcast_service.send_whatsapp_template_message')
    def test_prepare_creates_queue_without_sender(self, mocked_sender):
        call_command(
            'process_buyer_broadcasts',
            '--campaign-id',
            self.campaign.pk,
            '--prepare',
        )
        self.assertEqual(self.campaign.recipients.count(), 1)
        mocked_sender.assert_not_called()

    @override_settings(BUYER_BROADCAST_MODE='OFF')
    @patch('core.services.buyer_broadcast_service.send_whatsapp_template_message')
    def test_send_only_in_test_mode(self, mocked_sender):
        self.campaign.status = BUYER_BROADCAST_STATUS_QUEUED
        self.campaign.save(update_fields=['status'])
        BuyerBroadcastRecipient.objects.create(
            campaign=self.campaign,
            buyer=self.buyer,
            phone_snapshot=self.buyer.phone_normalized,
            masked_phone_snapshot='7701***0000',
            status=BUYER_BROADCAST_RECIPIENT_QUEUED,
            queued_at=timezone.now(),
        )
        with self.assertRaises(CommandError):
            call_command(
                'process_buyer_broadcasts',
                '--campaign-id',
                self.campaign.pk,
                '--send',
            )
        mocked_sender.assert_not_called()

    def test_prepare_and_send_together_forbidden(self):
        with self.assertRaises(CommandError):
            call_command(
                'process_buyer_broadcasts',
                '--campaign-id',
                self.campaign.pk,
                '--prepare',
                '--send',
            )

    @patch(
        'core.services.buyer_broadcast_service.send_whatsapp_template_message',
        return_value={'ok': True, 'message_id': 'wamid.TEST123', 'status_code': 200},
    )
    def test_recipient_id_limits_processing(self, mocked_sender):
        second = make_buyer(is_test_contact=True)
        grant_marketing(second)
        self.campaign.test_contacts.add(second)
        call_command(
            'process_buyer_broadcasts',
            '--campaign-id',
            self.campaign.pk,
            '--prepare',
        )
        recipient = self.campaign.recipients.filter(status=BUYER_BROADCAST_RECIPIENT_QUEUED).first()
        out = StringIO()
        call_command(
            'process_buyer_broadcasts',
            '--campaign-id',
            self.campaign.pk,
            '--send',
            '--recipient-id',
            recipient.pk,
            stdout=out,
        )
        self.assertEqual(mocked_sender.call_count, 1)

    def test_stdout_does_not_contain_full_phone(self):
        out = StringIO()
        call_command(
            'process_buyer_broadcasts',
            '--campaign-id',
            self.campaign.pk,
            stdout=out,
        )
        self.assertNotIn(self.buyer.phone_normalized, out.getvalue())


@override_settings(BUYER_BROADCAST_MODE='TEST', BUYER_BROADCAST_TEST_MAX_RECIPIENTS=5)
class BuyerBroadcastAdminTests(TestCase):
    def setUp(self):
        self.campaign = make_campaign()
        self.buyer = make_buyer(is_test_contact=True, primary_city='Алматы')
        grant_marketing(self.buyer)
        self.campaign.test_contacts.set([self.buyer])
        self.client = Client()
        self.admin = default_admin_site._registry[BuyerBroadcastCampaign]

    def test_preview_requires_login(self):
        url = reverse('admin:core_buyerbroadcastcampaign_preview', args=[self.campaign.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_preview_denied_without_permission(self):
        user = User.objects.create_user(username='noperm', password='pass')
        self.client.force_login(user)
        url = reverse('admin:core_buyerbroadcastcampaign_preview', args=[self.campaign.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_preview_html_masks_phone(self):
        user = User.objects.create_superuser(
            username='admin',
            password='pass',
            email='admin@example.com',
        )
        self.client.force_login(user)
        url = reverse('admin:core_buyerbroadcastcampaign_preview', args=[self.campaign.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.buyer.phone_normalized)

    def test_prepare_has_confirmation(self):
        user = User.objects.create_superuser(
            username='admin2',
            password='pass',
            email='admin2@example.com',
        )
        self.client.force_login(user)
        url = reverse('admin:core_buyerbroadcastcampaign_prepare', args=[self.campaign.pk])
        response = self.client.get(url)
        self.assertContains(response, 'Подтвердить подготовку очереди')

    def test_admin_preview_has_no_send_button(self):
        user = User.objects.create_superuser(
            username='admin3',
            password='pass',
            email='admin3@example.com',
        )
        self.client.force_login(user)
        url = reverse('admin:core_buyerbroadcastcampaign_preview', args=[self.campaign.pk])
        response = self.client.get(url)
        self.assertNotContains(response, '--send')

    def test_recipient_manual_create_forbidden(self):
        from core.admin import BuyerBroadcastRecipientAdmin

        recipient_admin = BuyerBroadcastRecipientAdmin(BuyerBroadcastRecipient, default_admin_site)
        request = self.client.get('/').wsgi_request
        request.user = User.objects.create_superuser(
            username='admin4',
            password='pass',
            email='admin4@example.com',
        )
        self.assertFalse(recipient_admin.has_add_permission(request))
