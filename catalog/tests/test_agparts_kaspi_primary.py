"""Kaspi-first ZPT prices: isolated test DB, no collector or production writes."""
import json
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.template.loader import render_to_string
from django.utils import timezone

from catalog.agparts_kaspi_primary import (
    ALIGNED, AMBIGUOUS, BUNDLE_MIGRATION, NO_FLOOR, NO_FRESH_PRICE,
    NOT_FILTER, READY, UNDER_FLOOR, PriceReconciliationError,
    _merchant_card, apply_previewed_prices, evaluate_one, fingerprint, price_review,
)
from catalog.models import Product, ProductKaspiListing, ProductPriceTier, SellerProfile
from catalog.templatetags.agparts_primary_price import get_agparts_verified_retail
from catalog.templatetags.kaspi_buy import kaspi_buy_url
from catalog.templatetags.product_extras import product_json_ld
from repricer.models import KaspiCompetitorOfferSnapshot


class AgpartsKaspiPrimaryPricingTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.profile = SellerProfile.objects.create(
            user=User.objects.create_user(username="price-sync-owner"),
            slug="ag-parts", name="AG Parts", phone="0000000000", city="Алматы",
        )
        self.filter = Product.objects.create(
            seller_profile=self.profile, seller_name="AG Parts", title="Салонный фильтр AG Parts",
            whatsapp_number="0000000000", article="301001199AA", status="active",
            price=1547, city="Алматы",
        )
        self.tier = ProductPriceTier.objects.create(
            product=self.filter, min_qty=1, price=390, is_active=True,
        )
        self.listing = ProductKaspiListing.objects.create(
            product=self.filter, master_sku="119714061_988697284",
            merchant_sku="301001199AA", is_active=True,
            public_url="https://kaspi.kz/shop/p/salonnyi-fil-tr-test-119714061/?c=750000000&m=30363568&ms=true",
            public_url_verified_at=self.now-timedelta(days=10),
        )
        self.offer = KaspiCompetitorOfferSnapshot.objects.create(
            listing=self.listing, seller_name="AG Parts", seller_code="30363568",
            price=Decimal("1300.00"), is_available=True,
            captured_at=self.now-timedelta(minutes=10), source="office_collector",
        )

    def decide(self):
        return evaluate_one(
            Product.objects.select_related("seller_profile")
            .prefetch_related("kaspi_listings", "price_tiers")
            .get(pk=self.filter.pk),
            now=self.now
        )

    def test_kaspi_price_is_primary_for_matching_exact_filter_listing(self):
        d = self.decide()
        self.assertEqual(d.status, READY)
        self.assertEqual((d.zpt_price, d.kaspi_price), (1547, 1300))
        self.assertEqual(d.wholesale_floor, 390)
        self.assertEqual(d.listing_id, self.listing.id)
        self.assertEqual(d.master_sku, self.listing.master_sku)

    def test_no_default_write_with_readonly_preview(self):
        before = self.filter.price
        self.assertEqual([d.status for d in price_review(now=self.now)], [READY])
        self.filter.refresh_from_db()
        self.assertEqual(self.filter.price, before)
        self.assertEqual(self.offer.price, Decimal("1300.00"))

    def test_wrong_seller_offer_never_sets_product_price(self):
        self.offer.seller_code = "another-merchant"
        self.offer.save(update_fields=["seller_code"])
        d = self.decide()
        self.assertEqual(d.status, NO_FRESH_PRICE)
        self.assertIsNone(d.kaspi_price)

    def test_price_from_another_merchant_url_never_used(self):
        self.listing.public_url = self.listing.public_url.replace("m=30363568", "m=11111111")
        self.listing.save(update_fields=["public_url"])
        self.assertEqual(self.decide().status, NO_FRESH_PRICE)

    def test_no_guessed_link_from_sku(self):
        self.listing.public_url = ""
        self.listing.save(update_fields=["public_url"])
        self.assertEqual(self.decide().status, NO_FRESH_PRICE)

    def test_unverified_link_cannot_set_main_price(self):
        self.listing.public_url_verified_at = None
        self.listing.save(update_fields=["public_url_verified_at"])
        self.assertEqual(self.decide().status, NO_FRESH_PRICE)

    def test_stale_or_future_offer_is_ignored(self):
        for stamp in (self.now-timedelta(days=2), self.now+timedelta(hours=1)):
            with self.subTest(stamp=stamp):
                self.offer.captured_at = stamp
                self.offer.save(update_fields=["captured_at"])
                self.assertEqual(self.decide().status, NO_FRESH_PRICE)

    def test_wholesale_minimum_is_hard_floor(self):
        self.tier.price = 1400
        self.tier.save(update_fields=["price"])
        self.assertEqual(self.decide().status, UNDER_FLOOR)

    def test_missing_wholesale_minimum_blocks_price_change(self):
        self.tier.delete()
        self.assertEqual(self.decide().status, NO_FLOOR)

    def test_already_equal_price_does_not_require_a_write(self):
        self.filter.price = 1300
        self.filter.save(update_fields=["price"])
        self.tier.delete()
        self.assertEqual(self.decide().status, ALIGNED)

    def test_multiple_fresh_conflicting_listings_are_ambiguous(self):
        other = ProductKaspiListing.objects.create(
            product=self.filter, master_sku="alternate-master-123456",
            merchant_sku="301001199AA", is_active=True,
            public_url="https://kaspi.kz/shop/p/other-123456/?m=30363568&ms=true",
            public_url_verified_at=self.now,
        )
        KaspiCompetitorOfferSnapshot.objects.create(
            listing=other, seller_name="AG Parts", seller_code="30363568",
            price=Decimal("1700.00"), is_available=True,
            captured_at=self.now, source="office_collector",
        )
        self.assertEqual(self.decide().status, AMBIGUOUS)

    def test_old_second_card_is_not_an_ambiguity(self):
        other = ProductKaspiListing.objects.create(
            product=self.filter, master_sku="old-second", merchant_sku="301001199AA",
            is_active=True,
            public_url="https://kaspi.kz/shop/p/old-456789/?m=30363568&ms=true",
            public_url_verified_at=self.now,
        )
        KaspiCompetitorOfferSnapshot.objects.create(
            listing=other, seller_name="AG Parts", seller_code="30363568",
            price=Decimal("1700.00"), is_available=True,
            captured_at=self.now-timedelta(days=40), source="office_collector",
        )
        self.assertEqual(self.decide().status, READY)

    def test_bundle_of_four_spark_plugs_can_never_replace_single_zpt_unit(self):
        spark = Product.objects.create(
            seller_profile=self.profile, seller_name="AG Parts",
            title="Свеча зажигания AG Parts", article="F4J163707010",
            whatsapp_number="0000000000", price=2450, status="active",
        )
        ProductPriceTier.objects.create(product=spark, min_qty=1, price=740, is_active=True)
        link = ProductKaspiListing.objects.create(
            product=spark, master_sku="835932711", merchant_sku="F4J163707010",
            public_url="https://kaspi.kz/shop/p/svecha-set-133904131/?m=30363568&ms=true",
            public_url_verified_at=self.now, is_active=True,
        )
        KaspiCompetitorOfferSnapshot.objects.create(
            listing=link, seller_code="30363568", seller_name="AG Parts",
            price=Decimal("12450.00"), captured_at=self.now,
            is_available=True, source="office_collector",
        )
        d = next(x for x in price_review(now=self.now) if x.article=="F4J163707010")
        self.assertEqual(d.status, BUNDLE_MIGRATION)
        self.assertEqual(d.kaspi_price, 12450)
        self.assertEqual(d.zpt_price, 2450)
        with override_settings(AGPARTS_KASPI_PRICE_APPLY_ENABLED=True):
            done = apply_previewed_prices(expected_hash=fingerprint(price_review(now=self.now)), now=self.now)
        self.assertNotIn(spark.id, [x.product_id for x in done])
        spark.refresh_from_db()
        self.assertEqual(spark.price, 2450)

    def test_invalid_spark_packaging_not_infer_one_piece_sale(self):
        self.filter.title = "Свеча зажигания AG Parts"
        self.filter.save(update_fields=["title"])
        self.assertEqual(self.decide().status, NOT_FILTER)

    def test_price_application_disabled_without_env_flag(self):
        with override_settings(AGPARTS_KASPI_PRICE_APPLY_ENABLED=False):
            with self.assertRaises(PriceReconciliationError):
                apply_previewed_prices(expected_hash=fingerprint(price_review(now=self.now)), now=self.now)
        self.filter.refresh_from_db()
        self.assertEqual(self.filter.price, 1547)

    def test_manual_apply_rejects_wrong_preview_sha(self):
        with override_settings(AGPARTS_KASPI_PRICE_APPLY_ENABLED=True):
            with self.assertRaises(PriceReconciliationError):
                apply_previewed_prices(expected_hash="0" * 64, now=self.now)
        self.filter.refresh_from_db()
        self.assertEqual(self.filter.price, 1547)

    def test_explicit_apply_updates_only_eligible_zpt_price(self):
        decisions = price_review(now=self.now)
        expect = fingerprint(decisions)
        with override_settings(AGPARTS_KASPI_PRICE_APPLY_ENABLED=True):
            applied = apply_previewed_prices(expected_hash=expect, now=self.now)
        self.assertEqual([x.product_id for x in applied], [self.filter.pk])
        self.filter.refresh_from_db()
        self.assertEqual(self.filter.price, 1300)
        self.listing.refresh_from_db()
        self.offer.refresh_from_db()
        self.tier.refresh_from_db()
        self.assertEqual(self.tier.price, 390)
        self.assertEqual(self.offer.price, Decimal("1300.00"))
        self.assertFalse(self.listing.publish_to_kaspi)

    def test_zpt_product_card_price_uses_kaspi_not_stale_product_price(self):
        offer = get_agparts_verified_retail(self.filter)
        self.assertEqual(offer["price"], 1300)
        self.assertEqual(offer["kaspi_url"], self.listing.public_url)
        self.assertIsNone(offer["package_quantity"])
        self.assertEqual(kaspi_buy_url(self.filter), self.listing.public_url)
        html = render_to_string(
            "catalog/includes/product_price_display.html",
            {"product": self.filter, "price_class": "detail-price"},
        )
        self.assertIn("₸", html)
        self.assertNotIn("1547", "".join(html.split()))

    def test_zpt_card_never_falls_back_to_stale_product_price(self):
        self.offer.captured_at = self.now - timedelta(days=8)
        self.offer.save(update_fields=["captured_at"])
        offer = get_agparts_verified_retail(self.filter)
        self.assertIsNone(offer["price"])
        self.assertEqual(offer["kaspi_url"], "")
        self.assertEqual(kaspi_buy_url(self.filter), "")
        html = render_to_string(
            "catalog/includes/product_price_display.html",
            {"product": self.filter},
        )
        self.assertIn("Цена Kaspi уточняется", html)
        self.assertNotIn("1547", "".join(html.split()))

    def test_agparts_schema_product_uses_kaspi_main_price_only(self):
        data = json.loads(product_json_ld(self.filter))
        self.assertEqual(data["offers"]["price"], 1300)
        self.assertNotIn("availability", data["offers"])
        self.offer.captured_at = self.now - timedelta(days=8)
        self.offer.save(update_fields=["captured_at"])
        refreshed = Product.objects.get(pk=self.filter.pk)
        self.assertNotIn("offers", json.loads(product_json_ld(refreshed)))

    def test_four_plug_bundle_shows_pack_price_only_and_no_local_stock_promise(self):
        product = Product.objects.create(
            seller_profile=self.profile, seller_name="AG Parts",
            title="Свеча зажигания", article="F4J163707010",
            whatsapp_number="0000000000", status="active", price=2450,
        )
        ProductPriceTier.objects.create(product=product, min_qty=1, price=740, is_active=True)
        listing = ProductKaspiListing.objects.create(
            product=product, master_sku="835932711", merchant_sku=product.article,
            is_active=True, public_url_verified_at=self.now,
            public_url="https://kaspi.kz/shop/p/four-plugs-133904131/?m=30363568&ms=true",
        )
        KaspiCompetitorOfferSnapshot.objects.create(
            listing=listing, seller_code="30363568", seller_name="AG Parts",
            price=Decimal("12450.00"), is_available=True,
            captured_at=self.now, source="office_collector",
        )
        info = get_agparts_verified_retail(product)
        self.assertEqual(info["price"], 12450)
        self.assertEqual(info["package_quantity"], 4)
        self.assertEqual(kaspi_buy_url(product), listing.public_url)
        html = render_to_string("catalog/includes/product_price_display.html", {"product": product})
        self.assertIn("комплект 4 шт.", html)
        compact = "".join(html.split())
        self.assertIn(">12450₸", compact)
        self.assertNotIn(">2450₸", compact)
        structured = json.loads(product_json_ld(product))
        self.assertEqual(structured["offers"]["price"], 12450)
        self.assertNotIn("availability", structured["offers"])

    def test_four_pack_still_identified_when_collector_has_no_current_price(self):
        product = Product.objects.create(
            seller_profile=self.profile, seller_name="AG Parts",
            title="Свеча зажигания", article="F4J163707010",
            whatsapp_number="0000000000", status="active", price=2450,
        )
        info = get_agparts_verified_retail(product)
        self.assertIsNone(info["price"])
        self.assertEqual(info["package_quantity"], 4)
        self.assertEqual(info["kaspi_url"], "")

    def test_other_sellers_keep_original_zpt_price_template(self):
        other = SellerProfile.objects.create(
            user=User.objects.create_user(username="other-price-seller"),
            slug="other-price-seller", name="Other Seller",
            phone="0000000000", city="Астана",
        )
        product = Product.objects.create(
            seller_profile=other, seller_name="Other Seller",
            title="Фильтр другого продавца", article="OTHERSKU",
            whatsapp_number="0000000000", status="active", price=2400,
        )
        self.assertIsNone(get_agparts_verified_retail(product))
        html = render_to_string("catalog/includes/product_price_display.html", {"product": product})
        self.assertIn("2 400", html.replace(chr(160), " "))

    def test_whitelist_of_valid_merchant_links(self):
        self.assertTrue(_merchant_card(self.listing.public_url, "30363568"))
        for bad in ("http://kaspi.kz/shop/p/test-100/", 
                    "https://kaspi.kz.attacker.example/shop/p/test-100/?m=30363568&ms=true",
                    "https://kaspi.kz/shop/api/?m=30363568",
                    "https://kaspi.kz/shop/p/test-100/?m=30363568&m=1234&ms=true",
                    "https://kaspi.kz/shop/p/test-100/?m=30363568"):
            with self.subTest(url=bad):
                self.assertFalse(_merchant_card(bad, "30363568"))

