from __future__ import annotations

import json
import re
from importlib import import_module
from unittest.mock import patch

from django.apps import apps
from django.test import TestCase

from core.whatsapp_template_management import build_meta_template_payload
from marketing.models import (
    MarketingCampaign,
    MarketingCampaignMessage,
    MarketingCampaignRecipient,
    MarketingCampaignSendRun,
    MarketingWhatsAppTemplate,
)


SEED = import_module('marketing.migrations.0018_seller_platform_confirm_template')
UPDATE = import_module('marketing.migrations.0019_update_seller_platform_confirm_body')

EMOJI_RE = re.compile(
    '['
    '\U0001F300-\U0001FAFF'
    '\u2600-\u27BF'
    '\uFE0F'
    '\u200D'
    ']'
)


def _seller_platform_template():
    return MarketingWhatsAppTemplate.objects.get(
        meta_template_name=SEED.TEMPLATE_META_NAME,
        language_code=SEED.TEMPLATE_LANGUAGE,
    )


class SellerPlatformConfirmTemplateTests(TestCase):
    def test_seeded_template_matches_meta_review_spec(self):
        template = _seller_platform_template()
        self.assertEqual(template.meta_template_name, 'zpt_seller_platform_confirm_v1')
        self.assertEqual(template.language_code, 'ru')
        self.assertEqual(template.category, 'marketing')
        self.assertEqual(template.meta_status, 'unknown')
        self.assertNotEqual(template.meta_status, 'approved')
        self.assertNotEqual(template.meta_status, 'pending')
        self.assertTrue(template.is_active)
        self.assertTrue(template.allow_test_campaign)
        self.assertEqual(template.allowed_purposes, list(SEED.TEMPLATE_ALLOWED_PURPOSES))
        self.assertEqual(template.header_text, SEED.TEMPLATE_HEADER)
        self.assertEqual(template.body_text, UPDATE.TEMPLATE_BODY)
        self.assertNotEqual(template.body_text, SEED.TEMPLATE_BODY)
        self.assertEqual(template.footer_text, '')
        self.assertEqual(template.variables, [])
        self.assertEqual(template.meta_template_id, '')
        self.assertLessEqual(len(template.header_text), SEED.MAX_HEADER_LENGTH)
        self.assertLessEqual(len(template.body_text), UPDATE.MAX_BODY_LENGTH)
        self.assertEqual(len(template.buttons), 2)
        self.assertEqual(template.buttons, SEED.TEMPLATE_BUTTONS)
        for button in template.buttons:
            self.assertEqual(button['type'], 'quick_reply')
        for link in SEED.STABLE_GO_LINKS:
            self.assertIn(link, template.body_text)
        for text in (template.header_text, template.body_text, template.footer_text):
            self.assertIsNone(EMOJI_RE.search(text))
        for button in template.buttons:
            self.assertIsNone(EMOJI_RE.search(button['text']))
            self.assertIsNone(EMOJI_RE.search(button['value']))

    def test_meta_payload_is_marketing_quick_reply_without_internal_values(self):
        template = _seller_platform_template()
        with patch(
            'core.whatsapp_template_management.urllib.request.urlopen',
        ) as mocked_urlopen:
            payload = build_meta_template_payload(template)
            mocked_urlopen.assert_not_called()
        self.assertEqual(payload['name'], SEED.TEMPLATE_META_NAME)
        self.assertEqual(payload['language'], 'ru')
        self.assertEqual(payload['category'], 'MARKETING')
        types = [item['type'] for item in payload['components']]
        self.assertEqual(types, ['HEADER', 'BODY', 'BUTTONS'])
        self.assertEqual(payload['components'][0]['format'], 'TEXT')
        self.assertEqual(payload['components'][0]['text'], SEED.TEMPLATE_HEADER)
        self.assertEqual(payload['components'][1]['text'], UPDATE.TEMPLATE_BODY)
        buttons = payload['components'][2]['buttons']
        self.assertEqual(len(buttons), 2)
        self.assertEqual(
            buttons,
            [
                {'type': 'QUICK_REPLY', 'text': 'Да, подтверждаю'},
                {'type': 'QUICK_REPLY', 'text': 'Нет, отключить'},
            ],
        )
        dumped = json.dumps(payload)
        self.assertNotIn('seller_confirm_yes', dumped)
        self.assertNotIn('seller_confirm_no', dumped)
        self.assertNotIn('"value"', dumped)
        self.assertNotIn('graph.facebook.com', dumped)

    def test_seed_is_idempotent_and_does_not_create_campaigns_or_contact_meta(self):
        with patch(
            'core.whatsapp_template_management.urllib.request.urlopen',
        ) as mocked_urlopen:
            UPDATE.update_seller_platform_confirm_body(apps, None)
            UPDATE.update_seller_platform_confirm_body(apps, None)
            mocked_urlopen.assert_not_called()

        self.assertEqual(
            MarketingWhatsAppTemplate.objects.filter(
                meta_template_name=SEED.TEMPLATE_META_NAME,
                language_code=SEED.TEMPLATE_LANGUAGE,
            ).count(),
            1,
        )
        template = _seller_platform_template()
        self.assertEqual(template.header_text, SEED.TEMPLATE_HEADER)
        self.assertEqual(template.body_text, UPDATE.TEMPLATE_BODY)
        self.assertEqual(
            MarketingCampaign.objects.filter(message_template=template).count(),
            0,
        )
        self.assertEqual(
            MarketingCampaignRecipient.objects.filter(
                campaign__message_template=template,
            ).count(),
            0,
        )
        self.assertEqual(
            MarketingCampaignSendRun.objects.filter(template=template).count(),
            0,
        )
        self.assertEqual(
            MarketingCampaignMessage.objects.filter(send_run__template=template).count(),
            0,
        )

    def test_approved_or_pending_content_is_not_overwritten(self):
        template = _seller_platform_template()
        original_body = 'Already reviewed by Meta.'
        template.body_text = original_body
        template.header_text = 'Keep this header'
        template.meta_status = 'approved'
        template.meta_template_id = 'already-submitted'
        template.save(update_fields=[
            'body_text',
            'header_text',
            'meta_status',
            'meta_template_id',
            'updated_at',
        ])

        SEED.seed_seller_platform_confirm_template(apps, None)
        template.refresh_from_db()
        self.assertEqual(template.body_text, original_body)
        self.assertEqual(template.header_text, 'Keep this header')
        self.assertEqual(template.meta_status, 'approved')
        self.assertEqual(template.meta_template_id, 'already-submitted')

        template.meta_status = 'pending'
        template.save(update_fields=['meta_status', 'updated_at'])
        SEED.seed_seller_platform_confirm_template(apps, None)
        template.refresh_from_db()
        self.assertEqual(template.body_text, original_body)
        self.assertEqual(template.meta_status, 'pending')

    def test_unsubmitted_draft_is_brought_to_exact_content(self):
        template = _seller_platform_template()
        template.name = 'Old local draft name'
        template.meta_status = 'draft'
        template.header_text = 'Old header'
        template.body_text = 'Old body'
        template.footer_text = 'Old footer'
        template.buttons = []
        template.allowed_purposes = ['request_sellers']
        template.allow_test_campaign = False
        template.meta_template_id = ''
        template.save()

        SEED.seed_seller_platform_confirm_template(apps, None)
        template.refresh_from_db()
        self.assertEqual(template.name, SEED.TEMPLATE_NAME)
        self.assertEqual(template.meta_status, 'unknown')
        self.assertEqual(template.header_text, SEED.TEMPLATE_HEADER)
        self.assertEqual(template.body_text, SEED.TEMPLATE_BODY)
        self.assertEqual(template.footer_text, '')
        self.assertEqual(template.buttons, SEED.TEMPLATE_BUTTONS)
        self.assertEqual(template.allowed_purposes, list(SEED.TEMPLATE_ALLOWED_PURPOSES))
        self.assertTrue(template.allow_test_campaign)
        self.assertEqual(template.meta_template_id, '')

    def test_body_update_changes_only_body_text(self):
        template = _seller_platform_template()
        snapshot = {
            'name': template.name,
            'header_text': template.header_text,
            'footer_text': template.footer_text,
            'buttons': list(template.buttons),
            'variables': list(template.variables),
            'allowed_purposes': list(template.allowed_purposes),
            'is_active': template.is_active,
            'allow_test_campaign': template.allow_test_campaign,
            'category': template.category,
            'meta_status': template.meta_status,
            'meta_template_id': template.meta_template_id,
            'language_code': template.language_code,
            'meta_template_name': template.meta_template_name,
        }
        template.body_text = 'Temporary local body before copy update.'
        template.save(update_fields=['body_text', 'updated_at'])

        UPDATE.update_seller_platform_confirm_body(apps, None)
        template.refresh_from_db()
        self.assertEqual(template.body_text, UPDATE.TEMPLATE_BODY)
        for field, value in snapshot.items():
            self.assertEqual(getattr(template, field), value)

    def test_body_update_skips_non_editable_and_submitted_templates(self):
        template = _seller_platform_template()
        protected_body = 'Do not overwrite this body.'
        for status in ('pending', 'approved', 'rejected', 'paused', 'disabled'):
            template.body_text = protected_body
            template.meta_status = status
            template.meta_template_id = ''
            template.save(update_fields=[
                'body_text',
                'meta_status',
                'meta_template_id',
                'updated_at',
            ])
            UPDATE.update_seller_platform_confirm_body(apps, None)
            template.refresh_from_db()
            self.assertEqual(template.body_text, protected_body, status)
            self.assertEqual(template.meta_status, status)

        template.body_text = protected_body
        template.meta_status = 'unknown'
        template.meta_template_id = 'already-has-id'
        template.save(update_fields=[
            'body_text',
            'meta_status',
            'meta_template_id',
            'updated_at',
        ])
        UPDATE.update_seller_platform_confirm_body(apps, None)
        template.refresh_from_db()
        self.assertEqual(template.body_text, protected_body)
        self.assertEqual(template.meta_template_id, 'already-has-id')
