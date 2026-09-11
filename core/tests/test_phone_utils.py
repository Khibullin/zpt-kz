from urllib.parse import quote

from django.test import SimpleTestCase

from core.phone_utils import build_whatsapp_url, normalize_phone_for_whatsapp
from core.whatsapp_template_sender import normalize_whatsapp_phone


class NormalizePhoneForWhatsappTests(SimpleTestCase):
    def test_kz_leading_eight(self):
        self.assertEqual(normalize_phone_for_whatsapp('87772320709'), '77772320709')

    def test_plus_seven(self):
        self.assertEqual(normalize_phone_for_whatsapp('+77772320709'), '77772320709')

    def test_already_normalized(self):
        self.assertEqual(normalize_phone_for_whatsapp('77772320709'), '77772320709')

    def test_spaced_leading_eight(self):
        self.assertEqual(normalize_phone_for_whatsapp('8 777 232 07 09'), '77772320709')

    def test_formatted_plus_seven(self):
        self.assertEqual(
            normalize_phone_for_whatsapp('+7 (777) 232-07-09'),
            '77772320709',
        )

    def test_formatted_seven(self):
        self.assertEqual(
            normalize_phone_for_whatsapp('7 (777) 232-07-09'),
            '77772320709',
        )

    def test_ten_digit_local_gets_country_code_seven(self):
        self.assertEqual(normalize_phone_for_whatsapp('7772320709'), '77772320709')

    def test_uzbekistan_international(self):
        self.assertEqual(
            normalize_phone_for_whatsapp('+998901234567'),
            '998901234567',
        )

    def test_kyrgyzstan_international(self):
        self.assertEqual(
            normalize_phone_for_whatsapp('+996555123456'),
            '996555123456',
        )

    def test_ukraine_international(self):
        self.assertEqual(
            normalize_phone_for_whatsapp('+380501234567'),
            '380501234567',
        )

    def test_us_nanp_international(self):
        self.assertEqual(
            normalize_phone_for_whatsapp('+12025550123'),
            '12025550123',
        )

    def test_international_numbers_do_not_get_prefix_seven(self):
        self.assertEqual(normalize_phone_for_whatsapp('+998901234567'), '998901234567')
        self.assertEqual(normalize_phone_for_whatsapp('+996555123456'), '996555123456')
        self.assertEqual(normalize_phone_for_whatsapp('+380501234567'), '380501234567')
        self.assertEqual(normalize_phone_for_whatsapp('+12025550123'), '12025550123')
        self.assertFalse(normalize_phone_for_whatsapp('+998901234567').startswith('7'))
        self.assertFalse(normalize_phone_for_whatsapp('+996555123456').startswith('7'))

    def test_zero_zero_international_prefix_is_invalid(self):
        self.assertIsNone(normalize_phone_for_whatsapp('0012025550123'))

    def test_none_and_blank_are_invalid(self):
        self.assertIsNone(normalize_phone_for_whatsapp(None))
        self.assertIsNone(normalize_phone_for_whatsapp(''))

    def test_letters_are_invalid(self):
        self.assertIsNone(normalize_phone_for_whatsapp('abc'))

    def test_too_short_is_invalid(self):
        self.assertIsNone(normalize_phone_for_whatsapp('12345'))
        self.assertIsNone(normalize_phone_for_whatsapp('77723'))

    def test_does_not_treat_longer_eight_prefix_as_country_code(self):
        self.assertIsNone(normalize_phone_for_whatsapp('899890123456'))


class BuildWhatsappUrlTests(SimpleTestCase):
    def test_kz_eight_prefix_becomes_wa_me_seven(self):
        self.assertEqual(
            build_whatsapp_url('87772320709'),
            'https://wa.me/77772320709',
        )

    def test_encodes_message_once_with_percent_encoding(self):
        text = 'Здравствуйте! Нужна деталь №1'
        encoded = quote(text)
        url = build_whatsapp_url('87772320709', text)
        query = url.split('text=', 1)[1]
        self.assertEqual(url, f'https://wa.me/77772320709?text={encoded}')
        self.assertEqual(query, encoded)
        self.assertNotEqual(query, quote(encoded))
        self.assertNotIn(' ', url)
        self.assertIn('%20', query)
        self.assertNotIn('+', query)

    def test_invalid_returns_empty(self):
        self.assertEqual(build_whatsapp_url(None), '')
        self.assertEqual(build_whatsapp_url(''), '')
        self.assertEqual(build_whatsapp_url('abc'), '')
        self.assertNotIn('wa.me/None', build_whatsapp_url(None))
        self.assertNotEqual(build_whatsapp_url('87772320709'), 'https://wa.me/87772320709')


class NormalizeWhatsappPhoneDestinationTests(SimpleTestCase):
    def test_kz_leading_eight_is_digits_only_without_plus(self):
        self.assertEqual(normalize_whatsapp_phone('87772320709'), '77772320709')
        self.assertFalse(normalize_whatsapp_phone('87772320709').startswith('+'))

    def test_plus_seven_is_digits_only_without_plus(self):
        self.assertEqual(normalize_whatsapp_phone('+77772320709'), '77772320709')
        self.assertFalse(normalize_whatsapp_phone('+77772320709').startswith('+'))

    def test_uzbekistan_stays_international_digits(self):
        self.assertEqual(normalize_whatsapp_phone('+998901234567'), '998901234567')
        self.assertFalse(normalize_whatsapp_phone('+998901234567').startswith('+'))
        self.assertFalse(normalize_whatsapp_phone('+998901234567').startswith('7'))
