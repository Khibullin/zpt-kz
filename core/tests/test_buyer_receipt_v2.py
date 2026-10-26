from unittest.mock import patch
from django.test import TestCase, override_settings
from core.models import Request
from marketing.models import MarketingWhatsAppTemplate
from core.services.buyer_receipt import send_receipt, BODY, TEMPLATE_NAME

class BuyerReceiptTests(TestCase):
    def setUp(self):
        self.req = Request.objects.create(brand='Ford', model='Explorer', city='Алматы', phone='77001112233')
        self.template, _ = MarketingWhatsAppTemplate.objects.get_or_create(
            meta_template_name=TEMPLATE_NAME, language_code='ru', defaults={'name':'Buyer receipt test'})
        self.template.body_text = BODY
        self.template.meta_status = 'approved'
        self.template.is_active = True
        self.template.save()

    @patch('core.services.buyer_receipt.send_whatsapp_template_message')
    def test_manual_mode_buyer_only_and_deduplication(self, sender):
        sender.return_value = {'ok':True, 'message_id':'test'}
        with override_settings(MANUAL_REQUEST_PROCESSING=True):
            self.assertTrue(send_receipt(self.req)['ok'])
            self.assertEqual(send_receipt(self.req)['error_code'], 'buyer_receipt_already_attempted')
        sender.assert_called_once()
        args, kwargs = sender.call_args
        self.assertEqual(args[0], '77001112233')
        self.assertTrue(kwargs['buyer_receipt'])
        self.assertEqual(kwargs['template_name'], TEMPLATE_NAME)
        self.assertIn('/step/sellers/', kwargs['body_parameters'][1]['text'])

    @patch('core.services.buyer_receipt.send_whatsapp_template_message')
    def test_unapproved_or_changed_text_never_sent(self, sender):
        self.template.body_text = 'Другой текст'
        self.template.save()
        self.assertEqual(send_receipt(self.req)['error_code'], 'buyer_receipt_template_not_approved')
        sender.assert_not_called()
