from django.test import TestCase
from unittest.mock import patch
from core.views import _send_buyer_whatsapp_notification

class ManualReceiptTests(TestCase):
    @patch('core.whatsapp_template_sender.send_whatsapp_template_message')
    def test_automatic_receipt_disabled(self, send):
        self.assertEqual(_send_buyer_whatsapp_notification(object(), 0)['error_code'], 'buyer_receipt_manual_only')
        send.assert_not_called()
