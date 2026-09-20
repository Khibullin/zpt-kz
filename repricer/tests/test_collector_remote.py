from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from integrations.kaspi_competitors import (
    CompetitorPriceSourceRateLimited,
    CompetitorPriceSourceUnavailable,
    KaspiCompetitorOffer,
)
from repricer.collector_remote import (
    ABORT_ACCESS,
    ABORT_METHOD,
    ABORT_RATE_LIMIT,
    ABORT_SOURCE_THRESHOLD,
    CollectScanResult,
    CollectorManifest,
    CollectorRemoteError,
    RemoteListing,
    assert_collector_base_url,
    collect_remote_listings,
    parse_manifest_payload,
)

TOKEN = "collector-secret-token-xyz"


class ScriptedOfferSource:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def fetch_offers(self, *, master_sku, merchant_sku="", public_url=""):
        self.calls.append((master_sku, merchant_sku))
        del public_url
        if not self.script:
            raise AssertionError("unexpected extra Kaspi request")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return list(item)


class FakeOfferSource:
    def __init__(self, offers=None, error=None):
        self.offers = offers
        self.error = error
        self.calls = []

    def fetch_offers(self, *, master_sku, merchant_sku="", public_url=""):
        self.calls.append((master_sku, merchant_sku))
        del public_url
        if self.error is not None:
            raise self.error
        return list(self.offers or [])


class FakeZptClient:
    def __init__(
        self,
        listings,
        *,
        own_merchant_ids=None,
        undercut_amount=300,
        from_envelope=True,
    ):
        self.listings = listings
        self.own_merchant_ids = (
            list(own_merchant_ids) if own_merchant_ids is not None else ["TEST-OWN"]
        )
        self.undercut_amount = undercut_amount
        self.from_envelope = from_envelope
        self.posts = []

    def fetch_manifest(self, listing_ids=None):
        if listing_ids:
            wanted = set(listing_ids)
            listings = [item for item in self.listings if item.listing_id in wanted]
        else:
            listings = list(self.listings)
        return CollectorManifest(
            listings=listings,
            own_merchant_ids=list(self.own_merchant_ids),
            undercut_amount=self.undercut_amount,
            from_envelope=self.from_envelope,
        )

    def post_batch(self, payload):
        self.posts.append(payload)
        return {
            "ok": True,
            "duplicate": False,
            "snapshots_created": len(payload.get("offers") or []),
            "batch_id": payload["batch_id"],
            "listing_id": payload["listing_id"],
        }


def _listing(listing_id=1, **kwargs):
    defaults = {
        "listing_id": listing_id,
        "article": "X0390000206",
        "master_sku": "115801437_271928151",
        "merchant_sku": "X0390000206",
        "last_known_our_price": 3034,
    }
    defaults.update(kwargs)
    return RemoteListing(**defaults)


def _offers():
    return [
        KaspiCompetitorOffer(
            seller_name="TEST-MERCHANT",
            seller_code="TEST-OWN",
            price=Decimal("3034"),
            position=2,
        ),
        KaspiCompetitorOffer(
            seller_name="Other Shop",
            seller_code="30308762",
            price=Decimal("3033"),
            position=1,
        ),
    ]


class CollectorRemoteTests(TestCase):
    def test_command_requires_listing_ids_or_all_active(self):
        with self.assertRaises(CommandError):
            call_command("collect_kaspi_competitors_remote")

    def test_command_rejects_batch_size_over_10(self):
        with self.assertRaises(CommandError):
            call_command(
                "collect_kaspi_competitors_remote",
                "--listing-id",
                "1",
                "--batch-size",
                "11",
            )

    def test_command_rejects_all_active_with_listing_id(self):
        with self.assertRaises(CommandError):
            call_command(
                "collect_kaspi_competitors_remote",
                "--all-active",
                "--listing-id",
                "1",
            )

    @override_settings(
        KASPI_COMPETITOR_COLLECTOR_TOKEN=TOKEN,
        ZPT_KASPI_COLLECTOR_BASE_URL="https://zpt.kz",
        DEBUG=False,
    )
    def test_command_does_not_print_token(self):
        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "repricer.management.commands.collect_kaspi_competitors_remote.collect_remote_listings",
            return_value=CollectScanResult(listings=[]),
        ), patch(
            "repricer.management.commands.collect_kaspi_competitors_remote.KaspiPublicOfferSource"
        ):
            call_command(
                "collect_kaspi_competitors_remote",
                "--listing-id",
                "1",
                stdout=stdout,
                stderr=stderr,
            )
        combined = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(TOKEN, combined)
        self.assertNotIn("Bearer ", combined)

    @override_settings(
        KASPI_COMPETITOR_COLLECTOR_TOKEN=TOKEN,
        ZPT_KASPI_COLLECTOR_BASE_URL="https://zpt.kz",
        DEBUG=False,
    )
    def test_command_maps_collector_http_error_to_command_error(self):
        stdout = StringIO()
        with patch(
            "repricer.management.commands.collect_kaspi_competitors_remote.collect_remote_listings",
            side_effect=CollectorRemoteError("ZPT collector HTTP 401: unauthorized"),
        ), patch(
            "repricer.management.commands.collect_kaspi_competitors_remote.KaspiPublicOfferSource"
        ):
            with self.assertRaises(CommandError) as caught:
                call_command(
                    "collect_kaspi_competitors_remote",
                    "--all-active",
                    "--dry-run",
                    stdout=stdout,
                )
        self.assertIn("401", str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn(TOKEN, stdout.getvalue())

    def test_fetches_manifest_and_posts_when_offers_exist(self):
        client = FakeZptClient([_listing()])
        source = FakeOfferSource(_offers())
        stdout = StringIO()
        with patch("repricer.collector_remote.time.sleep"):
            scan = collect_remote_listings(
                listing_ids=[1],
                source=source,
                client=client,
                dry_run=False,
                sleep_seconds=1.0,
                stdout=stdout,
            )
        results = scan.listings
        self.assertEqual(len(source.calls), 1)
        self.assertEqual(source.calls[0][0], "115801437_271928151")
        self.assertEqual(len(client.posts), 1)
        payload = client.posts[0]
        self.assertEqual(payload["listing_id"], 1)
        self.assertEqual(payload["master_sku"], "115801437_271928151")
        self.assertEqual(len(payload["offers"]), 2)
        self.assertEqual(payload["offers"][1]["seller_code"], "30308762")
        self.assertEqual(payload["offers"][1]["price"], "3033")
        self.assertTrue(results[0].posted)
        self.assertEqual(results[0].snapshots_created, 2)

    def test_zero_offers_does_not_post(self):
        client = FakeZptClient([_listing()])
        source = FakeOfferSource([])
        stdout = StringIO()
        collect_remote_listings(
            listing_ids=[1],
            source=source,
            client=client,
            dry_run=False,
            sleep_seconds=1.0,
            stdout=stdout,
        )
        self.assertEqual(client.posts, [])
        self.assertIn("NO_OFFERS", stdout.getvalue())

    def test_source_405_does_not_post(self):
        client = FakeZptClient([_listing()])
        source = FakeOfferSource(
            error=CompetitorPriceSourceUnavailable("Kaspi отклонил read-only запрос (405)")
        )
        stdout = StringIO()
        collect_remote_listings(
            listing_ids=[1],
            source=source,
            client=client,
            dry_run=False,
            sleep_seconds=1.0,
            stdout=stdout,
        )
        self.assertEqual(client.posts, [])
        self.assertIn("SOURCE_ERROR", stdout.getvalue())
        self.assertNotIn(TOKEN, stdout.getvalue())

    def test_dry_run_does_not_post(self):
        client = FakeZptClient([_listing()])
        source = FakeOfferSource(_offers())
        stdout = StringIO()
        collect_remote_listings(
            listing_ids=[1],
            source=source,
            client=client,
            dry_run=True,
            sleep_seconds=1.0,
            stdout=stdout,
        )
        self.assertEqual(client.posts, [])
        self.assertIn("DRY-RUN", stdout.getvalue())
        self.assertIn("best_foreign_price=3033", stdout.getvalue())
        self.assertIn("last_known_our_price=3034", stdout.getvalue())
        self.assertIn("own_found=YES", stdout.getvalue())
        self.assertIn("own_offer_price=3034", stdout.getvalue())
        self.assertIn("foreign_offers=1", stdout.getvalue())
        self.assertIn("recommended_price=2733", stdout.getvalue())
        self.assertNotIn("best_seller=", stdout.getvalue())

    def test_sleep_floor_between_listings(self):
        client = FakeZptClient([_listing(1), _listing(2)])
        source = FakeOfferSource(_offers())
        with patch("repricer.collector_remote.time.sleep") as mocked_sleep:
            collect_remote_listings(
                listing_ids=[1, 2],
                source=source,
                client=client,
                dry_run=True,
                sleep_seconds=1.0,
            )
        mocked_sleep.assert_called_once_with(1.0)

    def test_unresolved_mapping_does_not_call_kaspi_or_post(self):
        unresolved = RemoteListing(
            listing_id=10,
            article="1017110XEN01",
            master_sku="1017110XEN01",
            merchant_sku="1017110XEN01",
        )
        client = FakeZptClient([unresolved])
        source = FakeOfferSource(_offers())
        stdout = StringIO()
        collect_remote_listings(
            listing_ids=[10],
            source=source,
            client=client,
            dry_run=False,
            sleep_seconds=1.0,
            stdout=stdout,
        )
        self.assertEqual(source.calls, [])
        self.assertEqual(client.posts, [])
        self.assertIn("UNRESOLVED_MAPPING", stdout.getvalue())

    def test_bound_public_url_collects_oem_master_sku(self):
        listing = RemoteListing(
            listing_id=10,
            article="272774M400",
            master_sku="272774M400",
            merchant_sku="272774M400",
            public_url="https://kaspi.kz/shop/p/filtr-vozdushnyi-272774m400-987654321/",
        )
        client = FakeZptClient([listing])
        source = FakeOfferSource(_offers())
        stdout = StringIO()
        collect_remote_listings(
            listing_ids=[10],
            source=source,
            client=client,
            dry_run=True,
            sleep_seconds=1.0,
            stdout=stdout,
        )
        self.assertEqual(source.calls, [("272774M400", "272774M400")])
        self.assertIn("product_id=987654321", stdout.getvalue())
        self.assertNotIn("UNRESOLVED_MAPPING", stdout.getvalue())

    def test_all_active_uses_full_manifest_and_batches(self):
        listings = [_listing(index) for index in range(1, 12)]
        listings.append(
            RemoteListing(
                listing_id=99,
                article="X01-90000014",
                master_sku="X01-90000014",
                merchant_sku="X01-90000014",
            )
        )
        client = FakeZptClient(listings)
        source = FakeOfferSource(_offers())
        stdout = StringIO()
        with patch("repricer.collector_remote.time.sleep"):
            scan = collect_remote_listings(
                source=source,
                client=client,
                dry_run=True,
                sleep_seconds=1.0,
                stdout=stdout,
                all_active=True,
                batch_size=10,
            )
        results = scan.listings
        self.assertEqual(len(source.calls), 11)
        self.assertEqual(client.posts, [])
        self.assertEqual(
            sum(1 for item in results if item.skipped_reason == "unresolved_mapping"),
            1,
        )
        self.assertIn("BATCH 1/2", stdout.getvalue())
        self.assertIn("BATCH 2/2", stdout.getvalue())
        self.assertEqual(len(results), 12)
        self.assertEqual(
            sum(1 for item in results if item.skipped_reason != "unresolved_mapping"),
            11,
        )

    def test_unresolved_does_not_sleep(self):
        numeric = _listing(1)
        unresolved = RemoteListing(
            listing_id=2,
            article="P8104140",
            master_sku="P8104140",
            merchant_sku="P8104140",
        )
        client = FakeZptClient([numeric, unresolved])
        source = FakeOfferSource(_offers())
        with patch("repricer.collector_remote.time.sleep") as mocked_sleep:
            collect_remote_listings(
                listing_ids=[1, 2],
                source=source,
                client=client,
                dry_run=True,
                sleep_seconds=1.0,
            )
        mocked_sleep.assert_not_called()

    @override_settings(KASPI_OWN_MERCHANT_IDS="TEST-OWN", KASPI_OWN_MERCHANT_NAMES="TEST-MERCHANT")
    def test_own_only_offers_are_flagged_and_still_post(self):
        own_only = [
            KaspiCompetitorOffer(
                seller_name="TEST-MERCHANT",
                seller_code="TEST-OWN",
                price=Decimal("3410"),
                position=1,
            )
        ]
        client = FakeZptClient([_listing()])
        source = FakeOfferSource(own_only)
        with patch("repricer.collector_remote.time.sleep"):
            scan = collect_remote_listings(
                listing_ids=[1],
                source=source,
                client=client,
                dry_run=False,
                sleep_seconds=1.0,
            )
        results = scan.listings
        self.assertEqual(len(client.posts), 1)
        self.assertTrue(results[0].own_only)
        self.assertEqual(results[0].competitor_offers, 0)
        self.assertFalse(scan.aborted)


class ManifestContractTests(SimpleTestCase):
    def test_envelope_payload_is_parsed(self):
        manifest = parse_manifest_payload(
            {
                "own_merchant_ids": ["30363568"],
                "undercut_amount": 300,
                "listings": [
                    {
                        "listing_id": 1,
                        "article": "X0390000206",
                        "master_sku": "115801437_271928151",
                        "merchant_sku": "X0390000206",
                        "last_known_our_price": 3034,
                    }
                ],
            }
        )
        self.assertTrue(manifest.from_envelope)
        self.assertEqual(manifest.own_merchant_ids, ["30363568"])
        self.assertEqual(manifest.undercut_amount, 300)
        self.assertEqual(manifest.listings[0].last_known_our_price, 3034)

    def test_legacy_list_payload_has_no_metadata(self):
        manifest = parse_manifest_payload(
            [{"listing_id": 1, "article": "A", "master_sku": "1", "merchant_sku": "A"}]
        )
        self.assertFalse(manifest.from_envelope)
        self.assertEqual(manifest.own_merchant_ids, [])
        self.assertIsNone(manifest.undercut_amount)


class OwnSellerAndMappingCollectorTests(TestCase):
    def test_all_active_uses_production_manifest_own_ids(self):
        client = FakeZptClient(
            [_listing()],
            own_merchant_ids=["30363568"],
            undercut_amount=300,
        )
        source = FakeOfferSource(
            [
                KaspiCompetitorOffer(
                    seller_name="AG Parts",
                    seller_code="30363568",
                    price=Decimal("3034"),
                    position=2,
                ),
                KaspiCompetitorOffer(
                    seller_name="ИП ХАЛИБАЕВА",
                    seller_code="30308762",
                    price=Decimal("3033"),
                    position=1,
                ),
            ]
        )
        with patch("repricer.collector_remote.time.sleep"):
            scan = collect_remote_listings(
                source=source,
                client=client,
                dry_run=True,
                sleep_seconds=1.0,
                all_active=True,
            )
        self.assertEqual(scan.own_merchant_ids, ("30363568",))
        self.assertEqual(scan.undercut_amount, 300)
        row = scan.listings[0]
        self.assertEqual(row.state, "READY")
        self.assertEqual(row.best_seller_code, "30308762")
        self.assertEqual(row.best_price, Decimal("3033"))
        self.assertEqual(row.recommended_price, Decimal("2733"))
        self.assertEqual(row.last_known_our_price, 3034)
        self.assertEqual(row.own_price, Decimal("3034"))
        self.assertTrue(row.own_seller_found)

    @override_settings(KASPI_OWN_MERCHANT_IDS="WRONG-LOCAL", KASPI_REPRICER_UNDERCUT_AMOUNT=999)
    def test_all_active_ignores_local_wrong_own_id(self):
        client = FakeZptClient(
            [_listing(last_known_our_price=1150)],
            own_merchant_ids=["30363568"],
            undercut_amount=300,
        )
        source = FakeOfferSource(
            [
                KaspiCompetitorOffer(
                    seller_name="AG Parts",
                    seller_code="30363568",
                    price=Decimal("1150"),
                    position=1,
                ),
                KaspiCompetitorOffer(
                    seller_name="Other Shop",
                    seller_code="30327411",
                    price=Decimal("1954"),
                    position=2,
                ),
            ]
        )
        scan = collect_remote_listings(
            source=source,
            client=client,
            dry_run=True,
            sleep_seconds=1.0,
            all_active=True,
        )
        row = scan.listings[0]
        self.assertEqual(scan.own_merchant_ids, ("30363568",))
        self.assertEqual(scan.undercut_amount, 300)
        self.assertNotEqual(row.best_seller_code, "30363568")
        self.assertEqual(row.best_price, Decimal("1954"))
        self.assertEqual(row.recommended_price, Decimal("1654"))
        self.assertEqual(row.last_known_our_price, 1150)

    def test_all_active_rejects_legacy_list_manifest(self):
        client = FakeZptClient([_listing()], from_envelope=False)
        with self.assertRaises(CollectorRemoteError):
            collect_remote_listings(
                source=FakeOfferSource(_offers()),
                client=client,
                dry_run=True,
                sleep_seconds=1.0,
                all_active=True,
            )
        self.assertEqual(client.posts, [])

    def test_merchant_30363568_is_excluded(self):
        client = FakeZptClient([_listing()], own_merchant_ids=["30363568"])
        source = FakeOfferSource(
            [
                KaspiCompetitorOffer(
                    seller_name="AG Parts",
                    seller_code="30363568",
                    price=Decimal("3034"),
                    position=1,
                ),
                KaspiCompetitorOffer(
                    seller_name="ИП ХАЛИБАЕВА",
                    seller_code="30308762",
                    price=Decimal("3033"),
                    position=2,
                ),
            ]
        )
        scan = collect_remote_listings(
            listing_ids=[1],
            source=source,
            client=client,
            dry_run=True,
            sleep_seconds=1.0,
        )
        row = scan.listings[0]
        self.assertEqual(row.state, "READY")
        self.assertEqual(row.best_seller_code, "30308762")
        self.assertEqual(row.best_price, Decimal("3033"))
        self.assertNotEqual(row.best_seller_name, "AG Parts")

    def test_own_only_is_no_other_offers(self):
        client = FakeZptClient(
            [_listing(last_known_our_price=3410)],
            own_merchant_ids=["30363568"],
        )
        source = FakeOfferSource(
            [
                KaspiCompetitorOffer(
                    seller_name="AG Parts",
                    seller_code="30363568",
                    price=Decimal("3410"),
                    position=1,
                )
            ]
        )
        stdout = StringIO()
        scan = collect_remote_listings(
            listing_ids=[1],
            source=source,
            client=client,
            dry_run=True,
            sleep_seconds=1.0,
            stdout=stdout,
        )
        row = scan.listings[0]
        self.assertEqual(row.state, "NO_OTHER_OFFERS")
        self.assertTrue(row.own_only)
        self.assertIsNone(row.best_price)
        self.assertIsNone(row.recommended_price)
        self.assertIn("best_foreign_seller=—", stdout.getvalue())
        self.assertIn("recommended_price=—", stdout.getvalue())
        self.assertEqual(client.posts, [])

    def test_own_plus_foreign_uses_foreign_min(self):
        client = FakeZptClient(
            [_listing(last_known_our_price=3740)],
            own_merchant_ids=["30363568"],
        )
        source = FakeOfferSource(
            [
                KaspiCompetitorOffer(
                    seller_name="AG Parts",
                    seller_code="30363568",
                    price=Decimal("3740"),
                    position=1,
                ),
                KaspiCompetitorOffer(
                    seller_name="AMIOSPHY GROUP",
                    seller_code="30440420",
                    price=Decimal("6864"),
                    position=2,
                ),
            ]
        )
        scan = collect_remote_listings(
            listing_ids=[1],
            source=source,
            client=client,
            dry_run=True,
            sleep_seconds=1.0,
        )
        row = scan.listings[0]
        self.assertEqual(row.state, "READY")
        self.assertEqual(row.best_price, Decimal("6864"))
        self.assertEqual(row.recommended_price, Decimal("6564"))

    def test_own_cheapest_does_not_become_competitor(self):
        client = FakeZptClient(
            [_listing(last_known_our_price=1150)],
            own_merchant_ids=["30363568"],
        )
        source = FakeOfferSource(
            [
                KaspiCompetitorOffer(
                    seller_name="AG Parts",
                    seller_code="30363568",
                    price=Decimal("1150"),
                    position=1,
                ),
                KaspiCompetitorOffer(
                    seller_name="Other",
                    seller_code="30327411",
                    price=Decimal("1954"),
                    position=2,
                ),
                KaspiCompetitorOffer(
                    seller_name="Dear",
                    seller_code="999",
                    price=Decimal("2500"),
                    position=3,
                ),
            ]
        )
        scan = collect_remote_listings(
            listing_ids=[1],
            source=source,
            client=client,
            dry_run=True,
            sleep_seconds=1.0,
        )
        row = scan.listings[0]
        self.assertEqual(row.state, "READY")
        self.assertEqual(row.best_price, Decimal("1954"))
        self.assertEqual(row.recommended_price, Decimal("1654"))
        self.assertNotEqual(row.best_price, Decimal("1150"))

    def test_own_absent_with_foreign_is_ready(self):
        client = FakeZptClient([_listing()], own_merchant_ids=["30363568"])
        source = FakeOfferSource(
            [
                KaspiCompetitorOffer(
                    seller_name="ИП ХАЛИБАЕВА",
                    seller_code="30308762",
                    price=Decimal("3033"),
                    position=1,
                )
            ]
        )
        scan = collect_remote_listings(
            listing_ids=[1],
            source=source,
            client=client,
            dry_run=True,
            sleep_seconds=1.0,
        )
        row = scan.listings[0]
        self.assertEqual(row.state, "READY")
        self.assertFalse(row.own_seller_found)
        self.assertEqual(row.best_price, Decimal("3033"))
        self.assertEqual(row.recommended_price, Decimal("2733"))

    def test_last_known_our_price_is_not_own_offer_min(self):
        client = FakeZptClient(
            [_listing(last_known_our_price=3034)],
            own_merchant_ids=["30363568"],
        )
        source = FakeOfferSource(
            [
                KaspiCompetitorOffer(
                    seller_name="AG Parts",
                    seller_code="30363568",
                    price=Decimal("999"),
                    position=1,
                ),
                KaspiCompetitorOffer(
                    seller_name="Other",
                    seller_code="1",
                    price=Decimal("3033"),
                    position=2,
                ),
            ]
        )
        stdout = StringIO()
        scan = collect_remote_listings(
            listing_ids=[1],
            source=source,
            client=client,
            dry_run=True,
            sleep_seconds=1.0,
            stdout=stdout,
        )
        row = scan.listings[0]
        self.assertEqual(row.last_known_our_price, 3034)
        self.assertEqual(row.own_price, Decimal("999"))
        self.assertIn("last_known_our_price=3034", stdout.getvalue())
        self.assertIn("own_offer_price=999", stdout.getvalue())

    def test_numeric_article_skus_are_unresolved_and_skip_kaspi(self):
        unresolved = [
            RemoteListing(33, "8890649934", "8890649934", "8890649934"),
            RemoteListing(57, "8025530500", "8025530500", "8025530500"),
            RemoteListing(65, "1056025900", "1056025900", "1056025900"),
        ]
        client = FakeZptClient(unresolved, own_merchant_ids=["30363568"])
        source = FakeOfferSource(_offers())
        stdout = StringIO()
        with patch("repricer.collector_remote.time.sleep") as mocked_sleep:
            scan = collect_remote_listings(
                listing_ids=[33, 57, 65],
                source=source,
                client=client,
                dry_run=False,
                sleep_seconds=1.0,
                stdout=stdout,
            )
        self.assertEqual(source.calls, [])
        self.assertEqual(client.posts, [])
        mocked_sleep.assert_not_called()
        self.assertEqual(
            [item.skipped_reason for item in scan.listings],
            ["unresolved_mapping", "unresolved_mapping", "unresolved_mapping"],
        )
        self.assertEqual(scan.kaspi_attempted, 0)

    def test_expected_unresolved_sku_shapes_skip_kaspi(self):
        rows = [
            RemoteListing(10, "1017110XEN01", "1017110XEN01", "1017110XEN01"),
            RemoteListing(33, "8890649934", "8890649934", "8890649934"),
            RemoteListing(43, "X01-90000014", "X01-90000014", "X01-90000014"),
            RemoteListing(54, "P8104140", "P8104140", "P8104140"),
            RemoteListing(56, "ZJPCY5000055", "ZJPCY5000055", "ZJPCY5000055"),
            RemoteListing(57, "8025530500", "8025530500", "8025530500"),
            RemoteListing(58, "S111F2801031700", "S111F2801031700", "S111F2801031700"),
            RemoteListing(59, "PBC1109610", "PBC1109610", "PBC1109610"),
            RemoteListing(65, "1056025900", "1056025900", "1056025900"),
            RemoteListing(68, "D20T0120700", "D20T0120700", "D20T0120700"),
            RemoteListing(69, "EM2E8121211E", "EM2E8121211E", "EM2E8121211E"),
        ]
        client = FakeZptClient(rows + [_listing(1)], own_merchant_ids=["30363568"])
        source = FakeOfferSource(_offers())
        with patch("repricer.collector_remote.time.sleep"):
            scan = collect_remote_listings(
                listing_ids=[item.listing_id for item in rows] + [1],
                source=source,
                client=client,
                dry_run=True,
                sleep_seconds=1.0,
            )
        unresolved_ids = {
            item.listing_id
            for item in scan.listings
            if item.skipped_reason == "unresolved_mapping"
        }
        self.assertEqual(unresolved_ids, {10, 33, 43, 54, 56, 57, 58, 59, 65, 68, 69})
        self.assertEqual(len(source.calls), 1)
        self.assertEqual(client.posts, [])

    def test_dry_run_never_posts_or_writes_prices(self):
        client = FakeZptClient([_listing()], own_merchant_ids=["30363568"])
        source = FakeOfferSource(_offers())
        scan = collect_remote_listings(
            listing_ids=[1],
            source=source,
            client=client,
            dry_run=True,
            sleep_seconds=1.0,
        )
        self.assertEqual(client.posts, [])
        self.assertFalse(any(item.posted for item in scan.listings))


class CircuitBreakerTests(TestCase):
    def _listings(self, count):
        return [_listing(index) for index in range(1, count + 1)]

    def _collect(self, listings, source, *, dry_run=True):
        client = FakeZptClient(listings)
        stdout = StringIO()
        with patch("repricer.collector_remote.time.sleep"):
            scan = collect_remote_listings(
                listing_ids=[item.listing_id for item in listings],
                source=source,
                client=client,
                dry_run=dry_run,
                sleep_seconds=1.0,
                stdout=stdout,
                batch_size=10,
            )
        return scan, client, stdout

    def test_429_on_first_listing_stops_scan(self):
        listings = self._listings(4)
        source = ScriptedOfferSource(
            [CompetitorPriceSourceRateLimited("Kaspi rate limit", http_status=429)]
        )
        scan, client, stdout = self._collect(listings, source, dry_run=False)
        self.assertEqual(len(source.calls), 1)
        self.assertEqual(client.posts, [])
        self.assertEqual(scan.abort_reason, ABORT_RATE_LIMIT)
        self.assertEqual(scan.kaspi_attempted, 1)
        self.assertEqual(scan.listings[0].skipped_reason, "rate_limit")
        self.assertEqual(
            [item.skipped_reason for item in scan.listings[1:]],
            ["scan_aborted", "scan_aborted", "scan_aborted"],
        )
        self.assertIn("SCAN_ABORTED RATE_LIMIT_429", stdout.getvalue())

    def test_403_stops_scan(self):
        listings = self._listings(3)
        source = ScriptedOfferSource(
            [
                CompetitorPriceSourceUnavailable(
                    "Kaspi отклонил read-only запрос (403)",
                    http_status=403,
                )
            ]
        )
        scan, client, _stdout = self._collect(listings, source, dry_run=False)
        self.assertEqual(len(source.calls), 1)
        self.assertEqual(client.posts, [])
        self.assertEqual(scan.abort_reason, ABORT_ACCESS)
        self.assertEqual(scan.listings[0].skipped_reason, "access_403")

    def test_405_stops_scan(self):
        listings = self._listings(3)
        source = ScriptedOfferSource(
            [
                CompetitorPriceSourceUnavailable(
                    "Kaspi отклонил read-only запрос (405)",
                    http_status=405,
                )
            ]
        )
        scan, client, _stdout = self._collect(listings, source)
        self.assertEqual(len(source.calls), 1)
        self.assertEqual(client.posts, [])
        self.assertEqual(scan.abort_reason, ABORT_METHOD)
        self.assertEqual(scan.listings[0].skipped_reason, "method_405")

    def test_three_consecutive_timeouts_stop_scan(self):
        listings = self._listings(5)
        timeout = CompetitorPriceSourceUnavailable("Kaspi public offers недоступен: timeout")
        source = ScriptedOfferSource([timeout, timeout, timeout])
        scan, client, _stdout = self._collect(listings, source, dry_run=False)
        self.assertEqual(len(source.calls), 3)
        self.assertEqual(client.posts, [])
        self.assertEqual(scan.abort_reason, ABORT_SOURCE_THRESHOLD)
        self.assertEqual(scan.kaspi_attempted, 3)
        self.assertEqual(
            [item.skipped_reason for item in scan.listings[:3]],
            ["source_error", "source_error", "source_error"],
        )
        self.assertEqual(
            [item.skipped_reason for item in scan.listings[3:]],
            ["scan_aborted", "scan_aborted"],
        )

    def test_timeout_then_success_resets_consecutive_counter(self):
        listings = self._listings(4)
        timeout = CompetitorPriceSourceUnavailable("Kaspi public offers недоступен: timeout")
        source = ScriptedOfferSource([timeout, _offers(), timeout, _offers()])
        scan, client, _stdout = self._collect(listings, source)
        self.assertEqual(len(source.calls), 4)
        self.assertEqual(client.posts, [])
        self.assertFalse(scan.aborted)
        self.assertEqual(scan.listings[0].skipped_reason, "source_error")
        self.assertEqual(scan.listings[1].skipped_reason, "dry_run")
        self.assertEqual(scan.listings[2].skipped_reason, "source_error")
        self.assertEqual(scan.listings[3].skipped_reason, "dry_run")

    def test_no_offers_does_not_count_toward_error_threshold(self):
        listings = self._listings(4)
        source = ScriptedOfferSource([[], [], [], _offers()])
        scan, client, stdout = self._collect(listings, source, dry_run=False)
        self.assertEqual(len(source.calls), 4)
        self.assertEqual(len(client.posts), 1)
        self.assertEqual(client.posts[0]["listing_id"], 4)
        self.assertFalse(scan.aborted)
        self.assertEqual(
            [item.skipped_reason for item in scan.listings[:3]],
            ["no_offers", "no_offers", "no_offers"],
        )
        self.assertTrue(scan.listings[3].posted)
        self.assertIn("NO_OFFERS", stdout.getvalue() + scan.listings[0].state)

    def test_unresolved_does_not_http_or_increment_errors(self):
        unresolved = RemoteListing(
            listing_id=10,
            article="1017110XEN01",
            master_sku="1017110XEN01",
            merchant_sku="1017110XEN01",
        )
        timeout = CompetitorPriceSourceUnavailable("timeout")
        listings = [_listing(1), unresolved, _listing(2), _listing(3)]
        source = ScriptedOfferSource([timeout, timeout, _offers()])
        scan, client, stdout = self._collect(listings, source)
        self.assertEqual(len(source.calls), 3)
        self.assertEqual(client.posts, [])
        self.assertFalse(scan.aborted)
        unresolved_row = next(item for item in scan.listings if item.listing_id == 10)
        self.assertEqual(unresolved_row.skipped_reason, "unresolved_mapping")
        self.assertIn("UNRESOLVED_MAPPING", stdout.getvalue())

    def test_dry_run_breaker_does_not_post(self):
        listings = self._listings(3)
        source = ScriptedOfferSource(
            [CompetitorPriceSourceRateLimited("rate limited", http_status=429)]
        )
        scan, client, _stdout = self._collect(listings, source, dry_run=True)
        self.assertEqual(client.posts, [])
        self.assertEqual(scan.abort_reason, ABORT_RATE_LIMIT)
        self.assertFalse(any(item.posted for item in scan.listings))

    def test_success_path_still_walks_all_batches(self):
        listings = self._listings(11)
        source = FakeOfferSource(_offers())
        scan, client, stdout = self._collect(listings, source)
        self.assertEqual(len(source.calls), 11)
        self.assertEqual(client.posts, [])
        self.assertFalse(scan.aborted)
        self.assertEqual(scan.kaspi_attempted, 11)
        self.assertIn("BATCH 1/2", stdout.getvalue())
        self.assertIn("BATCH 2/2", stdout.getvalue())

    def test_single_5xx_continues_then_success_resets(self):
        listings = self._listings(2)
        source = ScriptedOfferSource(
            [
                CompetitorPriceSourceUnavailable(
                    "Kaspi вернул HTTP 503",
                    http_status=503,
                ),
                _offers(),
            ]
        )
        scan, client, _stdout = self._collect(listings, source)
        self.assertEqual(len(source.calls), 2)
        self.assertEqual(client.posts, [])
        self.assertFalse(scan.aborted)
        self.assertEqual(scan.listings[0].skipped_reason, "source_error")
        self.assertEqual(scan.listings[1].skipped_reason, "dry_run")


class CollectorBaseUrlTests(SimpleTestCase):
    def test_https_is_allowed(self):
        assert_collector_base_url("https://zpt.kz", allow_localhost=False)

    def test_http_remote_is_rejected(self):
        with self.assertRaises(CommandError):
            assert_collector_base_url("http://example.com", allow_localhost=True)

    def test_localhost_http_allowed_in_dev(self):
        assert_collector_base_url("http://127.0.0.1:8000", allow_localhost=True)
