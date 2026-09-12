from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from catalog.models import Product, ProductKaspiListing
from repricer.management.commands.bootstrap_kaspi_repricer_pilot import PILOT_LISTINGS


class BootstrapKaspiRepricerPilotTests(TestCase):
    def setUp(self):
        for item in PILOT_LISTINGS:
            Product.objects.create(
                title=f"Товар {item.article}",
                article=item.article,
                price=item.price,
                seller_name="AG Parts",
                whatsapp_number="+77000000000",
            )

    def test_dry_run_does_not_write(self):
        out = StringIO()
        call_command("bootstrap_kaspi_repricer_pilot", stdout=out)

        self.assertEqual(ProductKaspiListing.objects.count(), 0)
        self.assertIn("DRY RUN", out.getvalue())

    def test_apply_creates_exactly_eight_inactive_publication_mappings(self):
        call_command("bootstrap_kaspi_repricer_pilot", "--apply", stdout=StringIO())

        self.assertEqual(ProductKaspiListing.objects.count(), len(PILOT_LISTINGS))
        for item in PILOT_LISTINGS:
            listing = ProductKaspiListing.objects.get(master_sku=item.master_sku)
            self.assertEqual(listing.product.article, item.article)
            self.assertEqual(listing.merchant_sku, item.article)
            self.assertEqual(listing.last_known_our_price, item.price)
            self.assertTrue(listing.is_active)
            self.assertFalse(listing.publish_to_kaspi)

    def test_apply_is_idempotent(self):
        call_command("bootstrap_kaspi_repricer_pilot", "--apply", stdout=StringIO())
        call_command("bootstrap_kaspi_repricer_pilot", "--apply", stdout=StringIO())

        self.assertEqual(ProductKaspiListing.objects.count(), len(PILOT_LISTINGS))

    def test_missing_product_fails_before_any_write(self):
        Product.objects.filter(article=PILOT_LISTINGS[-1].article).delete()

        with self.assertRaises(CommandError):
            call_command("bootstrap_kaspi_repricer_pilot", "--apply", stdout=StringIO())

        self.assertEqual(ProductKaspiListing.objects.count(), 0)
