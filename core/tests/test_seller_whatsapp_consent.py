from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product, SellerProfile
from core.models import (
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_SOURCE_WHATSAPP,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    INBOUND_EVENT_STATUS_AMBIGUOUS,
    INBOUND_EVENT_STATUS_ERROR,
    INBOUND_EVENT_STATUS_IGNORED,
    INBOUND_EVENT_STATUS_PROCESSED,
    INBOUND_EVENT_STATUS_STALE,
    INBOUND_EVENT_STATUS_UNMATCHED,
    SELLER_CONFIRM_ACTION_NO,
    SELLER_CONFIRM_ACTION_YES,
    SELLER_PLATFORM_CONFIRM_TEMPLATE,
    Seller,
    SellerContactConsent,
    WhatsAppInboundEvent,
)
from marketing.models import MarketingCampaign, MarketingCampaignMessage

WEBHOOK_SECRET = 'test-meta-app-secret'
WEBHOOK_VERIFY_TOKEN = 'test-verify-token'
WEBHOOK_SETTINGS = {
    'META_APP_SECRET': WEBHOOK_SECRET,
    'WHATSAPP_WEBHOOK_VERIFY_TOKEN': WEBHOOK_VERIFY_TOKEN,
}


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    digest = hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
    return f'sha256={digest}'


def _payload_for(message: dict) -> dict:
    return {
        'object': 'whatsapp_business_account',
        'entry': [
            {
                'id': 'waba',
                'changes': [
                    {
                        'field': 'messages',
                        'value': {
                            'messaging_product': 'whatsapp',
                            'messages': [message],
                        },
                    }
                ],
            }
        ],
    }


def _button_message(
    phone: str,
    text: str,
    *,
    wamid: str,
    msg_type: str = 'button',
    timestamp: str = '1700000000',
) -> dict:
    message = {
        'from': phone,
        'id': wamid,
        'timestamp': str(timestamp),
        'type': msg_type,
    }
    if msg_type == 'button':
        message['button'] = {'text': text}
    elif msg_type == 'interactive':
        message['interactive'] = {
            'type': 'button_reply',
            'button_reply': {'id': 'btn', 'title': text},
        }
    elif msg_type == 'text':
        message['text'] = {'body': text}
    return message


def make_seller(**kwargs) -> Seller:
    defaults = {
        'name': 'Test seller',
        'whatsapp': '77011112233',
        'city': 'Алматы',
        'transport_type': 'car',
        'is_active': True,
        'is_paused': False,
        'receive_requests': False,
    }
    defaults.update(kwargs)
    return Seller.objects.create(**defaults)


@override_settings(**WEBHOOK_SETTINGS)
class SellerContactConsentModelTests(TestCase):
    def setUp(self):
        self.seller = make_seller()
        self.now = timezone.now()

    def test_granted_requires_consented_at(self):
        consent = SellerContactConsent(
            seller=self.seller,
            phone_normalized=self.seller.whatsapp,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_GRANTED,
        )
        with self.assertRaises(ValidationError):
            consent.full_clean()

    def test_revoked_requires_revoked_at(self):
        consent = SellerContactConsent(
            seller=self.seller,
            phone_normalized=self.seller.whatsapp,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_REVOKED,
        )
        with self.assertRaises(ValidationError):
            consent.full_clean()

    def test_unique_seller_phone_channel_purpose(self):
        SellerContactConsent.objects.create(
            seller=self.seller,
            phone_normalized=self.seller.whatsapp,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_GRANTED,
            consented_at=self.now,
        )
        with self.assertRaises(ValidationError):
            SellerContactConsent.objects.create(
                seller=self.seller,
                phone_normalized=self.seller.whatsapp,
                channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
                purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
                status=CONTACT_CONSENT_STATUS_GRANTED,
                consented_at=self.now,
            )


@override_settings(**WEBHOOK_SETTINGS)
class WhatsAppWebhookTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.url = reverse('whatsapp_webhook')
        self.seller = make_seller(
            whatsapp='77015556677',
            receive_requests=False,
            is_paused=True,
        )

    def _post(self, payload: dict, *, signature=None, secret: str = WEBHOOK_SECRET):
        body = json.dumps(payload).encode('utf-8')
        headers = {}
        if signature is not False:
            headers['HTTP_X_HUB_SIGNATURE_256'] = (
                signature if signature is not None else _sign(body, secret)
            )
        return self.client.post(
            self.url,
            data=body,
            content_type='application/json',
            **headers,
        )

    def test_get_correct_verify_token_returns_challenge(self):
        response = self.client.get(
            self.url,
            {
                'hub.mode': 'subscribe',
                'hub.verify_token': WEBHOOK_VERIFY_TOKEN,
                'hub.challenge': 'challenge-token-1',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), 'challenge-token-1')

    def test_get_wrong_verify_token_forbidden(self):
        response = self.client.get(
            self.url,
            {
                'hub.mode': 'subscribe',
                'hub.verify_token': 'wrong',
                'hub.challenge': 'challenge-token-1',
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_post_without_signature_forbidden(self):
        payload = _payload_for(
            _button_message(self.seller.whatsapp, 'Да, подтверждаю', wamid='wamid.nosig')
        )
        response = self._post(payload, signature=False)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(SellerContactConsent.objects.exists())

    def test_post_with_wrong_signature_forbidden(self):
        payload = _payload_for(
            _button_message(self.seller.whatsapp, 'Да, подтверждаю', wamid='wamid.badsig')
        )
        response = self._post(payload, signature='sha256=' + ('ab' * 32))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(SellerContactConsent.objects.exists())

    @override_settings(META_APP_SECRET='', WHATSAPP_WEBHOOK_VERIFY_TOKEN=WEBHOOK_VERIFY_TOKEN)
    def test_post_without_app_secret_fail_closed(self):
        payload = _payload_for(
            _button_message(self.seller.whatsapp, 'Да, подтверждаю', wamid='wamid.nosecret')
        )
        body = json.dumps(payload).encode('utf-8')
        response = self.client.post(
            self.url,
            data=body,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=_sign(body),
        )
        self.assertEqual(response.status_code, 403)

    def test_valid_hmac_accepts_payload(self):
        payload = _payload_for(
            _button_message(self.seller.whatsapp, 'Да, подтверждаю', wamid='wamid.ok')
        )
        response = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            WhatsAppInboundEvent.objects.filter(provider_message_id='wamid.ok').exists()
        )

    @patch('core.whatsapp_template_sender.send_whatsapp_template_message')
    def test_yes_button_grants_consent_and_unpauses(self, mocked_send):
        self.seller.is_active = False
        self.seller.save(update_fields=['is_active'])
        payload = _payload_for(
            _button_message(self.seller.whatsapp, 'Да, подтверждаю', wamid='wamid.yes')
        )
        campaigns_before = MarketingCampaign.objects.count()
        response = self._post(payload)
        self.assertEqual(response.status_code, 200)
        mocked_send.assert_not_called()
        self.assertEqual(MarketingCampaign.objects.count(), campaigns_before)
        self.assertFalse(MarketingCampaignMessage.objects.exists())

        consent = SellerContactConsent.objects.get()
        self.assertEqual(consent.seller_id, self.seller.pk)
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_GRANTED)
        self.assertEqual(consent.source, CONTACT_CONSENT_SOURCE_WHATSAPP)
        self.assertEqual(consent.consent_text_version, SELLER_PLATFORM_CONFIRM_TEMPLATE)
        self.assertEqual(consent.evidence_reference, 'whatsapp:wamid.yes')
        self.assertEqual(consent.phone_normalized, self.seller.whatsapp)
        self.assertIsNotNone(consent.consented_at)
        self.assertIsNone(consent.revoked_at)

        self.seller.refresh_from_db()
        self.assertTrue(self.seller.receive_requests)
        self.assertFalse(self.seller.is_paused)
        self.assertFalse(self.seller.is_active)

        event = WhatsAppInboundEvent.objects.get(provider_message_id='wamid.yes')
        self.assertEqual(event.processing_status, INBOUND_EVENT_STATUS_PROCESSED)
        self.assertEqual(event.action, SELLER_CONFIRM_ACTION_YES)

    @patch('core.whatsapp_template_sender.send_whatsapp_template_message')
    def test_no_button_revokes_consent_without_deleting_shop(self, mocked_send):
        user = User.objects.create_user('shop-owner', password='secret')
        profile = SellerProfile.objects.create(
            user=user,
            name='Shop',
            phone=self.seller.whatsapp,
            city='Алматы',
        )
        product = Product.objects.create(
            title='Part',
            slug='part-keep',
            article='KEEP-1',
            price=1000,
            seller_name='Shop',
            whatsapp_number=self.seller.whatsapp,
            status='active',
        )
        self.seller.receive_requests = True
        self.seller.is_paused = False
        self.seller.is_active = True
        self.seller.save(update_fields=['receive_requests', 'is_paused', 'is_active'])
        previous_consented_at = timezone.now() - timedelta(days=1)
        SellerContactConsent.objects.create(
            seller=self.seller,
            phone_normalized=self.seller.whatsapp,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_GRANTED,
            consented_at=previous_consented_at,
        )
        previous_consented_at = SellerContactConsent.objects.get().consented_at

        payload = _payload_for(
            _button_message(
                self.seller.whatsapp,
                'Нет, отключить',
                wamid='wamid.no',
                timestamp=str(int(timezone.now().timestamp())),
            )
        )
        response = self._post(payload)
        self.assertEqual(response.status_code, 200)
        mocked_send.assert_not_called()

        consent = SellerContactConsent.objects.get()
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_REVOKED)
        self.assertEqual(consent.source, CONTACT_CONSENT_SOURCE_WHATSAPP)
        self.assertEqual(consent.consent_text_version, SELLER_PLATFORM_CONFIRM_TEMPLATE)
        self.assertEqual(consent.evidence_reference, 'whatsapp:wamid.no')
        self.assertEqual(consent.consented_at, previous_consented_at)
        self.assertIsNotNone(consent.revoked_at)

        self.seller.refresh_from_db()
        self.assertFalse(self.seller.receive_requests)
        self.assertTrue(self.seller.is_paused)
        self.assertTrue(self.seller.is_active)
        self.assertTrue(Seller.objects.filter(pk=self.seller.pk).exists())
        self.assertTrue(SellerProfile.objects.filter(pk=profile.pk).exists())
        product.refresh_from_db()
        self.assertEqual(product.status, 'active')
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_plain_text_does_not_change_consent(self):
        payload = _payload_for(
            _button_message(
                self.seller.whatsapp,
                'Да, подтверждаю',
                wamid='wamid.text',
                msg_type='text',
            )
        )
        response = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SellerContactConsent.objects.exists())
        event = WhatsAppInboundEvent.objects.get(provider_message_id='wamid.text')
        self.assertEqual(event.processing_status, INBOUND_EVENT_STATUS_IGNORED)
        self.assertEqual(event.action, '')
        self.seller.refresh_from_db()
        self.assertFalse(self.seller.receive_requests)
        self.assertTrue(self.seller.is_paused)

    def test_unknown_button_text_ignored(self):
        payload = _payload_for(
            _button_message(self.seller.whatsapp, 'ок', wamid='wamid.unknown')
        )
        response = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SellerContactConsent.objects.exists())
        event = WhatsAppInboundEvent.objects.get(provider_message_id='wamid.unknown')
        self.assertEqual(event.processing_status, INBOUND_EVENT_STATUS_IGNORED)

    def test_unknown_phone_unmatched(self):
        payload = _payload_for(
            _button_message('77019998877', 'Да, подтверждаю', wamid='wamid.unmatched')
        )
        response = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SellerContactConsent.objects.exists())
        event = WhatsAppInboundEvent.objects.get(provider_message_id='wamid.unmatched')
        self.assertEqual(event.processing_status, INBOUND_EVENT_STATUS_UNMATCHED)

    def test_two_sellers_same_phone_ambiguous(self):
        make_seller(name='Second', whatsapp=self.seller.whatsapp)
        payload = _payload_for(
            _button_message(self.seller.whatsapp, 'Да, подтверждаю', wamid='wamid.amb')
        )
        response = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SellerContactConsent.objects.exists())
        event = WhatsAppInboundEvent.objects.get(provider_message_id='wamid.amb')
        self.assertEqual(event.processing_status, INBOUND_EVENT_STATUS_AMBIGUOUS)
        self.seller.refresh_from_db()
        self.assertFalse(self.seller.receive_requests)

    def test_duplicate_provider_message_id_is_idempotent(self):
        payload = _payload_for(
            _button_message(self.seller.whatsapp, 'Да, подтверждаю', wamid='wamid.dup')
        )
        self.assertEqual(self._post(payload).status_code, 200)
        consent = SellerContactConsent.objects.get()
        self.seller.receive_requests = False
        self.seller.is_paused = True
        self.seller.save(update_fields=['receive_requests', 'is_paused'])
        consent.status = CONTACT_CONSENT_STATUS_REVOKED
        consent.revoked_at = timezone.now()
        consent.save()

        self.assertEqual(self._post(payload).status_code, 200)
        self.assertEqual(
            WhatsAppInboundEvent.objects.filter(provider_message_id='wamid.dup').count(),
            1,
        )
        consent.refresh_from_db()
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_REVOKED)
        self.seller.refresh_from_db()
        self.assertFalse(self.seller.receive_requests)
        self.assertTrue(self.seller.is_paused)

    def test_button_type_payload(self):
        payload = _payload_for(
            _button_message(
                self.seller.whatsapp,
                'Да, подтверждаю',
                wamid='wamid.button-type',
                msg_type='button',
            )
        )
        self.assertEqual(self._post(payload).status_code, 200)
        self.assertEqual(
            SellerContactConsent.objects.get().status,
            CONTACT_CONSENT_STATUS_GRANTED,
        )

    def test_interactive_button_reply_payload(self):
        payload = _payload_for(
            _button_message(
                self.seller.whatsapp,
                'Нет, отключить',
                wamid='wamid.interactive',
                msg_type='interactive',
            )
        )
        self.assertEqual(self._post(payload).status_code, 200)
        self.assertEqual(
            SellerContactConsent.objects.get().status,
            CONTACT_CONSENT_STATUS_REVOKED,
        )
        event = WhatsAppInboundEvent.objects.get(provider_message_id='wamid.interactive')
        self.assertEqual(event.message_type, 'interactive')
        self.assertEqual(event.action, SELLER_CONFIRM_ACTION_NO)

    def test_nbsp_button_text_is_normalized(self):
        payload = _payload_for(
            _button_message(
                self.seller.whatsapp,
                'Да,\u00a0подтверждаю',
                wamid='wamid.nbsp',
            )
        )
        self.assertEqual(self._post(payload).status_code, 200)
        self.assertEqual(
            SellerContactConsent.objects.get().status,
            CONTACT_CONSENT_STATUS_GRANTED,
        )

    def test_processing_exception_keeps_seller_state_and_records_error_event(self):
        payload = _payload_for(
            _button_message(
                self.seller.whatsapp,
                'Да, подтверждаю',
                wamid='wamid.error-audit',
            )
        )
        with patch(
            'core.whatsapp_inbound._apply_seller_confirm_yes',
            side_effect=RuntimeError('boom'),
        ):
            response = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SellerContactConsent.objects.exists())
        self.seller.refresh_from_db()
        self.assertFalse(self.seller.receive_requests)
        self.assertTrue(self.seller.is_paused)

        event = WhatsAppInboundEvent.objects.get(provider_message_id='wamid.error-audit')
        self.assertEqual(event.processing_status, INBOUND_EVENT_STATUS_ERROR)
        self.assertEqual(event.phone_normalized, self.seller.whatsapp)
        self.assertEqual(event.message_type, 'button')
        self.assertEqual(event.button_text, 'Да, подтверждаю')
        self.assertEqual(event.action, SELLER_CONFIRM_ACTION_YES)
        self.assertIsNotNone(event.provider_timestamp)
        self.assertEqual(len(event.payload_hash), 64)

        self.assertEqual(self._post(payload).status_code, 200)
        self.assertEqual(
            WhatsAppInboundEvent.objects.filter(provider_message_id='wamid.error-audit').count(),
            1,
        )
        self.assertFalse(SellerContactConsent.objects.exists())
        self.seller.refresh_from_db()
        self.assertFalse(self.seller.receive_requests)
        self.assertTrue(self.seller.is_paused)

    def test_delayed_older_yes_does_not_override_newer_no(self):
        t1, t2 = '1700000000', '1700000100'
        self.assertEqual(
            self._post(
                _payload_for(
                    _button_message(
                        self.seller.whatsapp,
                        'Да, подтверждаю',
                        wamid='wamid.stale-yes-1',
                        timestamp=t1,
                    )
                )
            ).status_code,
            200,
        )
        self.assertEqual(
            self._post(
                _payload_for(
                    _button_message(
                        self.seller.whatsapp,
                        'Нет, отключить',
                        wamid='wamid.stale-no-2',
                        timestamp=t2,
                    )
                )
            ).status_code,
            200,
        )
        response = self._post(
            _payload_for(
                _button_message(
                    self.seller.whatsapp,
                    'Да, подтверждаю',
                    wamid='wamid.stale-yes-1-delayed',
                    timestamp=t1,
                )
            )
        )
        self.assertEqual(response.status_code, 200)
        consent = SellerContactConsent.objects.get()
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_REVOKED)
        self.seller.refresh_from_db()
        self.assertFalse(self.seller.receive_requests)
        self.assertTrue(self.seller.is_paused)
        delayed = WhatsAppInboundEvent.objects.get(
            provider_message_id='wamid.stale-yes-1-delayed'
        )
        self.assertEqual(delayed.processing_status, INBOUND_EVENT_STATUS_STALE)

    def test_delayed_older_no_does_not_override_newer_yes(self):
        t1, t2 = '1700000000', '1700000100'
        self.assertEqual(
            self._post(
                _payload_for(
                    _button_message(
                        self.seller.whatsapp,
                        'Нет, отключить',
                        wamid='wamid.stale-no-1',
                        timestamp=t1,
                    )
                )
            ).status_code,
            200,
        )
        self.assertEqual(
            self._post(
                _payload_for(
                    _button_message(
                        self.seller.whatsapp,
                        'Да, подтверждаю',
                        wamid='wamid.stale-yes-2',
                        timestamp=t2,
                    )
                )
            ).status_code,
            200,
        )
        response = self._post(
            _payload_for(
                _button_message(
                    self.seller.whatsapp,
                    'Нет, отключить',
                    wamid='wamid.stale-no-1-delayed',
                    timestamp=t1,
                )
            )
        )
        self.assertEqual(response.status_code, 200)
        consent = SellerContactConsent.objects.get()
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_GRANTED)
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.receive_requests)
        self.assertFalse(self.seller.is_paused)
        delayed = WhatsAppInboundEvent.objects.get(
            provider_message_id='wamid.stale-no-1-delayed'
        )
        self.assertEqual(delayed.processing_status, INBOUND_EVENT_STATUS_STALE)

    def test_equal_timestamp_yes_does_not_restore_after_revoke(self):
        timestamp = '1700000000'
        self.assertEqual(
            self._post(
                _payload_for(
                    _button_message(
                        self.seller.whatsapp,
                        'Нет, отключить',
                        wamid='wamid.equal-no',
                        timestamp=timestamp,
                    )
                )
            ).status_code,
            200,
        )
        response = self._post(
            _payload_for(
                _button_message(
                    self.seller.whatsapp,
                    'Да, подтверждаю',
                    wamid='wamid.equal-yes',
                    timestamp=timestamp,
                )
            )
        )
        self.assertEqual(response.status_code, 200)
        consent = SellerContactConsent.objects.get()
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_REVOKED)
        self.seller.refresh_from_db()
        self.assertFalse(self.seller.receive_requests)
        self.assertTrue(self.seller.is_paused)
        yes_event = WhatsAppInboundEvent.objects.get(provider_message_id='wamid.equal-yes')
        self.assertEqual(yes_event.processing_status, INBOUND_EVENT_STATUS_STALE)
