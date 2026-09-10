from types import SimpleNamespace

from django.test import SimpleTestCase

from catalog.templatetags.product_extras import (
    public_product_url,
    public_product_whatsapp_message,
)


class PublicProductUrlTests(SimpleTestCase):
    def test_legacy_slug_uses_canonical_alias(self):
        product = SimpleNamespace(slug='audi', pk=1981)

        self.assertEqual(
            public_product_url(product),
            '/peugeot-308-hu71151x/',
        )

    def test_regular_slug_is_unchanged(self):
        product = SimpleNamespace(slug='ordinary-product', pk=1)

        self.assertEqual(public_product_url(product), '/ordinary-product/')

    def test_empty_slug_falls_back_to_numeric_url(self):
        product = SimpleNamespace(slug='', pk=123)

        self.assertEqual(public_product_url(product), '/123/')

    def test_whatsapp_message_replaces_legacy_url_with_canonical_url(self):
        product = SimpleNamespace(
            slug='jac',
            pk=2047,
            get_whatsapp_inquiry_message=lambda: (
                'Здравствуйте! Ссылка на товар: https://zpt.kz/jac/'
            ),
        )

        message = public_product_whatsapp_message(product)

        self.assertIn(
            'https://zpt.kz/great-wall-poer-1017110xed95/',
            message,
        )
        self.assertNotIn('https://zpt.kz/jac/', message)

    def test_whatsapp_message_keeps_regular_product_url(self):
        product = SimpleNamespace(
            slug='ordinary-product',
            pk=1,
            get_whatsapp_inquiry_message=lambda: (
                'Здравствуйте! Ссылка на товар: https://zpt.kz/ordinary-product/'
            ),
        )

        self.assertEqual(
            public_product_whatsapp_message(product),
            'Здравствуйте! Ссылка на товар: https://zpt.kz/ordinary-product/',
        )
