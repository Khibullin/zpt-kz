from __future__ import annotations

from importlib import import_module

from django.test import TestCase

from core.whatsapp_template_management import build_meta_template_payload
from marketing.models import MarketingWhatsAppTemplate


SELLER_REQUEST = import_module('marketing.migrations.0022_seller_request_consent_template')
AG_PARTS = import_module('marketing.migrations.0012_prepare_ag_parts_wholesale_launch')


class WhatsAppTemplateVariableExamplesTests(TestCase):
    def test_seller_request_payload_contains_body_examples(self):
        template = MarketingWhatsAppTemplate.objects.get(
            meta_template_name=SELLER_REQUEST.TEMPLATE_META_NAME,
            language_code=SELLER_REQUEST.TEMPLATE_LANGUAGE,
        )

        payload = build_meta_template_payload(template)
        body = next(item for item in payload['components'] if item['type'] == 'BODY')

        self.assertEqual(
            body['example']['body_text'][0],
            [
                '431',
                'Chery',
                'Tiggo 7 Pro',
                'Тормоза',
                'Алматы',
                'Нужен пыльник на тормозной цилиндр задний | 🔗 Открыть заявку: https://zpt.kz/r/431/8micYM/',
                '+7 775 813 4694',
            ],
        )

    def test_template_without_variables_keeps_payload_without_example(self):
        template = MarketingWhatsAppTemplate.objects.get(
            meta_template_name=AG_PARTS.TEMPLATE_META_NAME,
            language_code=AG_PARTS.TEMPLATE_LANGUAGE,
        )

        payload = build_meta_template_payload(template)
        body = next(item for item in payload['components'] if item['type'] == 'BODY')

        self.assertNotIn('example', body)
