"""Tests for the seller-confirmed Kaspi URL binding."""
from django.test import SimpleTestCase
from catalog.kaspi_public_url import kaspi_product_id_from_public_url, validate_kaspi_public_url

CONFIRMED_URL = "https://kaspi.kz/shop/p/salonnyi-fil-tr-cd569f2801032700-138020172/"


class ConfirmedKaspiUrlTests(SimpleTestCase):
    def test_public_url_is_valid(self):
        validate_kaspi_public_url(CONFIRMED_URL)

    def test_public_product_id_differs_from_merchant_sku(self):
        self.assertEqual(kaspi_product_id_from_public_url(CONFIRMED_URL), "138020172")
        self.assertNotEqual("138020172", "423766246")
