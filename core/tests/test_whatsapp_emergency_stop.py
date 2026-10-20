from unittest.mock import patch

from django.test import TestCase

from core.models import BroadcastSettings
from core.whatsapp_template_sender import send_whatsapp_template_message


class WhatsAppEmergencyStopTests(TestCase):
    @patch('core.whatsapp_template_sender.urllib.request.urlopen')
    @patch('core.whatsapp_template_sender.validate_whatsapp_sender_config')
    def test_stop_blocks_every_template_before_network(self, config, http):
        control = BroadcastSettings.load()
        control.emergency_stop = True
        control.save()
        for template in ('buyer_receipt', 'seller_request', 'service_request', 'marketing'):
            result = send_whatsapp_template_message('77000000000', template_name=template)
            self.assertFalse(result['ok'])
            self.assertEqual(result['error_code'], 'whatsapp_emergency_stop')
            self.assertEqual(result['message_id'], '')
        config.assert_not_called()
        http.assert_not_called()

    @patch('core.whatsapp_template_sender.urllib.request.urlopen')
    @patch('core.whatsapp_template_sender.validate_whatsapp_sender_config', return_value=('123', 'fake'))
    def test_stop_is_read_again_after_previous_send(self, config, http):
        response = http.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = b'{"messages":[{"id":"test-message"}]}'
        first = send_whatsapp_template_message('77000000000', template_name='test')
        self.assertTrue(first['ok'])
        BroadcastSettings.objects.filter(pk=1).update(emergency_stop=True)
        second = send_whatsapp_template_message('77000000000', template_name='test')
        self.assertFalse(second['ok'])
        self.assertEqual(http.call_count, 1)
