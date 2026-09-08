from __future__ import annotations

import os
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.models import (
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    Request,
    RequestDispatch,
    Seller,
    SellerContactConsent,
)
from core.request_dispatch_service import send_single_dispatch
from core.seller_request_consent import (
    SELLER_REQUEST_CONSENT_NO_TEXT,
    SELLER_REQUEST_CONSENT_TEMPLATE,
    SELLER_REQUEST_CONSENT_YES_TEXT,
    seller_request_template_kwargs,
)
from core.whatsapp_inbound import (
    process_inbound_message,
    resolve_seller_confirm_action,
)
from marketing.models import MarketingWhatsAppTemplate


class SellerRequestConsentTemplateSeedTests(TestCase):
    def test_template_is_seeded_with_current_request_copy_and_opt_in(self):
        template = MarketingWhatsAppTemplate.objects.get(
            meta_template_name=SELLER_REQUEST_CONSENT_TEMPLATE,
            language_code='ru',
        )
        self.assertEqual(template.category, 'marketing')
        self.assertEqual(template.meta_status, 'unknown')
        self.assertIn('Новая заявка №{{1}} от клиента на автозапчасть', template.body_text)
        self.assertIn('Марка: {{2}}', template.body_text)
        self.assertIn('Комментарий клиента:\n{{6}}', template.body_text)
        self.assertIn('Телефон клиента:\n{{7}}', template.body_text)
        self.assertIn(
            'Хотите получать от ZPT.KZ заявки покупателей и выгодные предложения?',
            template.body_text,
        )
        self.assertEqual(
            [button['text'] for button in template.buttons],
            [SELLER_REQUEST_CONSENT_YES_TEXT, SELLER_REQUEST_CONSENT_NO_TEXT],
        )
        self.assertEqual(len(template.variables), 7)


class SellerRequestConsentRoutingTests(TestCase):
    def setUp(self):
        self.request = Request.objects.create(
            transport_type='car',
            brand='Chery',
            model='Tiggo 7 Pro',
            category='Тормоза',
            city='Алматы',
            description='Нужен пыльник на тормозной цилиндр задний',
            phone='77758134694',
        )
        self.seller = Seller.objects.create(
            name='Seller consent test',
            whatsapp='77011112233',
            transport_type='car',
            city='Алматы',
            is_active=True,
            is_paused=False,
            receive_requests=True,
        )

    def _dispatch(self, *, position=1):
        return RequestDispatch.objects.create(
            request=self.request,
            seller=self.seller,
            wave_number=1,
            position_number=position,
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at=timezone.now(),
        )

    @patch.dict(os.environ, {'WHATSAPP_SELLER_CONSENT_TEMPLATE_ENABLED': 'true'})
    def test_unknown_consent_routes_to_new_template(self):
        kwargs = seller_request_template_kwargs(self.seller)
        self.assertEqual(kwargs['template_name'], SELLER_REQUEST_CONSENT_TEMPLATE)
        self.assertFalse(kwargs['include_image_header'])
        self.assertEqual(len(kwargs['button_components']), 2)
        self.assertEqual(kwargs['button_components'][0]['sub_type'], 'quick_reply')

        dispatch = self._dispatch()
        with patch(
            'core.views.send_whatsapp_template',
            return_value={'ok': True, 'message_id': 'wamid.consent'},
        ) as mocked_send:
            result = send_single_dispatch(dispatch)

        self.assertTrue(result['ok'])
        call = mocked_send.call_args
        self.assertEqual(call.kwargs['template_name'], SELLER_REQUEST_CONSENT_TEMPLATE)
        self.assertFalse(call.kwargs['include_image_header'])
        self.assertEqual(len(call.kwargs['button_components']), 2)

    @patch.dict(os.environ, {'WHATSAPP_SELLER_CONSENT_TEMPLATE_ENABLED': 'true'})
    def test_granted_consent_keeps_existing_request_template(self):
        SellerContactConsent.objects.create(
            seller=self.seller,
            phone_normalized=self.seller.whatsapp,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_GRANTED,
            consented_at=timezone.now(),
        )
        self.assertEqual(seller_request_template_kwargs(self.seller), {})

        dispatch = self._dispatch()
        with patch(
            'core.views.send_whatsapp_template',
            return_value={'ok': True, 'message_id': 'wamid.normal'},
        ) as mocked_send:
            result = send_single_dispatch(dispatch)

        self.assertTrue(result['ok'])
        self.assertEqual(mocked_send.call_args.kwargs, {})

    @patch.dict(os.environ, {'WHATSAPP_SELLER_CONSENT_TEMPLATE_ENABLED': 'false'})
    def test_feature_flag_prevents_use_before_meta_approval(self):
        self.assertEqual(seller_request_template_kwargs(self.seller), {})


class SellerRequestConsentInboundTests(TestCase):
    def setUp(self):
        self.seller = Seller.objects.create(
            name='Inbound consent seller',
            whatsapp='77015556677',
            transport_type='car',
            city='Алматы',
            is_active=True,
            is_paused=True,
            receive_requests=False,
        )

    def _message(self, text: str, wamid: str) -> dict:
        return {
            'from': self.seller.whatsapp,
            'id': wamid,
            'timestamp': str(int(timezone.now().timestamp())),
            'type': 'button',
            'button': {'text': text},
        }

    def test_new_quick_reply_labels_are_recognized(self):
        self.assertEqual(resolve_seller_confirm_action('Да, получать'), 'seller_confirm_yes')
        self.assertEqual(resolve_seller_confirm_action('Отключить'), 'seller_confirm_no')
        # Backward compatibility with the previously prepared confirmation template.
        self.assertEqual(resolve_seller_confirm_action('Да, подтверждаю'), 'seller_confirm_yes')
        self.assertEqual(resolve_seller_confirm_action('Нет, отключить'), 'seller_confirm_no')

    def test_yes_reply_records_new_template_version_and_enables_requests(self):
        processed = process_inbound_message(
            self._message(SELLER_REQUEST_CONSENT_YES_TEXT, 'wamid.request-consent-yes'),
            payload_hash='hash-yes',
        )
        self.assertTrue(processed)
        consent = SellerContactConsent.objects.get(seller=self.seller)
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_GRANTED)
        self.assertEqual(consent.consent_text_version, SELLER_REQUEST_CONSENT_TEMPLATE)
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.receive_requests)
        self.assertFalse(self.seller.is_paused)

    def test_no_reply_records_new_template_version_and_disables_requests(self):
        processed = process_inbound_message(
            self._message(SELLER_REQUEST_CONSENT_NO_TEXT, 'wamid.request-consent-no'),
            payload_hash='hash-no',
        )
        self.assertTrue(processed)
        consent = SellerContactConsent.objects.get(seller=self.seller)
        self.assertEqual(consent.status, CONTACT_CONSENT_STATUS_REVOKED)
        self.assertEqual(consent.consent_text_version, SELLER_REQUEST_CONSENT_TEMPLATE)
        self.seller.refresh_from_db()
        self.assertFalse(self.seller.receive_requests)
        self.assertTrue(self.seller.is_paused)
