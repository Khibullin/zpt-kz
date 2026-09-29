from django.conf import settings
from django.test import SimpleTestCase

from core.buyer_receipt_template_v2 import (
    ACTIVATED,
    BODY_TEXT,
    BODY_VARIABLES,
    BUTTONS,
    FOOTER_TEXT,
    PREPARED_TEMPLATE_NAME,
    PRODUCTION_FOOTER_TEXT,
    PRODUCTION_TEMPLATE_NAME,
    READY_TO_SUBMIT,
    prepared_template_meta_components,
)
from marketing.services.templates.constants import BASE_RESERVED_SERVICE_TEMPLATE_NAMES


class BuyerReceiptTemplateV2Tests(SimpleTestCase):
    def test_v2_is_prepared_and_production_name_stays_approved_template(self):
        self.assertEqual(PRODUCTION_TEMPLATE_NAME, 'zpt_buyer_request_receipt')
        self.assertEqual(PREPARED_TEMPLATE_NAME, 'zpt_buyer_request_receipt_v2')
        self.assertIn(PRODUCTION_TEMPLATE_NAME, BASE_RESERVED_SERVICE_TEMPLATE_NAMES)
        self.assertIn(PREPARED_TEMPLATE_NAME, BASE_RESERVED_SERVICE_TEMPLATE_NAMES)
        self.assertFalse(ACTIVATED)
        self.assertFalse(READY_TO_SUBMIT)
        self.assertEqual(BODY_TEXT, '')
        self.assertEqual(FOOTER_TEXT, '')
        self.assertEqual(PRODUCTION_FOOTER_TEXT, 'ZPT.KZ — заявки на автозапчасти')
        self.assertEqual(len(BODY_VARIABLES), 5)
        self.assertEqual(
            [button['text'] for button in BUTTONS],
            ['Открыть заявку', 'Мои заявки'],
        )
        components = prepared_template_meta_components()
        self.assertEqual([item['type'] for item in components], ['BUTTONS'])
        self.assertNotIn('FOOTER', [item['type'] for item in components])
        self.assertNotIn(PRODUCTION_FOOTER_TEXT, str(components))

    def test_settings_name_is_not_switched_to_v2(self):
        self.assertEqual(settings.WHATSAPP_BUYER_TEMPLATE_NAME, PRODUCTION_TEMPLATE_NAME)
        self.assertNotEqual(settings.WHATSAPP_BUYER_TEMPLATE_NAME, PREPARED_TEMPLATE_NAME)
