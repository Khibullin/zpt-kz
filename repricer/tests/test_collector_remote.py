from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from integrations.kaspi_competitors import (
    CompetitorPriceSourceUnavailable,
    KaspiCompetitorOffer,
)
from repricer.collector_remote import (
    RemoteListing,
    assert_collector_base_url,
    collect_remote_listings,
)

TOKEN = "collector-secret-token-xyz"


class FakeOfferSource:
    def __init__(self, offers=None, error=None):
        self.offers = offers
        self.error = error
        self.calls = []

    def fetch_offers(self, *, master_sku, merchant_sku=""):
        self.calls.append((master_sku, merchant_sku))
        if self.error is not None:
            raise self.error
        return list(self.offers or [])


class FakeZptClient:
    def __init__(self, listings):
        self.listings = listings
        self.posts = []

    def fetch_manifest(self, listing_ids):
        wanted = set(listing_ids)
        return [item for item in self.listings if item.listing_id in wanted]

    def post_batch(self, payload):
        self.posts.append(payload)
        return {
            "ok": True,
            "duplicate": False,
            "snapshots_created": len(payload.get("offers") or []),
            "batch_id": payload["batch_id"],
            "listing_id": payload["listing_id"],
        }


def _listing(listing_id=1):
    return RemoteListing(
        listing_id=listing_id,
        article="X0390000206",
        master_sku="115801437_271928151",
        merchant_sku="X0390000206",
    )


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
    def test_command_requires_listing_ids(self):
        with self.assertRaises(CommandError):
            call_command("collect_kaspi_competitors_remote")

    def test_command_rejects_more_than_10_listings(self):
        args = []
        for index in range(11):
            args.extend(["--listing-id", str(index + 1)])
        with self.assertRaises(CommandError):
            call_command("collect_kaspi_competitors_remote", *args)

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
            return_value=[],
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

    def test_fetches_manifest_and_posts_when_offers_exist(self):
        client = FakeZptClient([_listing()])
        source = FakeOfferSource(_offers())
        stdout = StringIO()
        with patch("repricer.collector_remote.time.sleep"):
            results = collect_remote_listings(
                listing_ids=[1],
                source=source,
                client=client,
                dry_run=False,
                sleep_seconds=1.0,
                stdout=stdout,
            )
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
        self.assertIn("3033", stdout.getvalue())

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


class CollectorBaseUrlTests(SimpleTestCase):
    def test_https_is_allowed(self):
        assert_collector_base_url("https://zpt.kz", allow_localhost=False)

    def test_http_remote_is_rejected(self):
        with self.assertRaises(CommandError):
            assert_collector_base_url("http://example.com", allow_localhost=True)

    def test_localhost_http_allowed_in_dev(self):
        assert_collector_base_url("http://127.0.0.1:8000", allow_localhost=True)
