from django.test import SimpleTestCase

from catalog.templatetags.phone_extras import format_phone
from catalog.templatetags.product_extras import whatsapp_phone


class FormatPhoneTests(SimpleTestCase):
    def test_ag_parts_correct_number(self):
        self.assertEqual(format_phone('77713607040'), '+7 (771) 360-70-40')

    def test_formatted_input_normalizes(self):
        self.assertEqual(format_phone('+7 771 360 7040'), '+7 (771) 360-70-40')

    def test_distinct_777_operator_number(self):
        self.assertEqual(format_phone('77771360740'), '+7 (777) 136-07-40')


class WhatsappPhoneTests(SimpleTestCase):
    def test_strips_to_digits(self):
        self.assertEqual(whatsapp_phone('+7 771 360 7040'), '77713607040')
