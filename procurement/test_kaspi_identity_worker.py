"""No Kaspi API credentials required for identity metadata checks."""
import json
from types import SimpleNamespace
from django.test import SimpleTestCase
from .kaspi_identity_worker import validate_public_identity, read_public_product_page


class BoundKaspiIdentityTests(SimpleTestCase):
    def offer(self):
        return SimpleNamespace(
            brand='TEST', article='ABC-01', name='TEST Фильтр ABC-01',
            kaspi_url='https://kaspi.kz/shop/p/filtr-123456/')

    def html(self, product):
        return '<script type="application/ld+json">' + json.dumps({
            '@type': 'Product', **product,
        }) + '</script>'

    def test_exact_product_metadata_is_accepted_without_images_or_fitments(self):
        offer = self.offer()
        source = self.html({
            'name': 'TEST Фильтр ABC-01', 'brand': 'TEST', 'mpn': 'ABC-01',
        })
        evidence = validate_public_identity(offer, source)
        self.assertTrue(evidence['confirmed'])
        self.assertEqual(evidence['source_url'], offer.kaspi_url)

    def test_missing_article_does_not_confirm_match(self):
        offer = self.offer()
        self.assertIsNone(validate_public_identity(offer, self.html({
            'name': 'TEST Фильтр ABC-01', 'brand': 'TEST',
        })))

    def test_wrong_brand_or_pack_does_not_confirm_match(self):
        offer = self.offer()
        for product in (
            {'name': 'TEST Фильтр ABC-01', 'brand': 'OTHER', 'mpn': 'ABC-01'},
            {'name': 'TEST Фильтр 2 шт ABC-01', 'brand': 'TEST', 'mpn': 'ABC-01'},
        ):
            self.assertIsNone(validate_public_identity(offer, self.html(product)))

    def test_foreign_host_rejected_before_request(self):
        with self.assertRaises(ValueError):
            read_public_product_page('https://example.org/shop/p/example-123/')
