from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from integrations.kaspi_competitors import KaspiPublicOfferSource
from repricer.collector_remote import (
    MAX_LISTINGS,
    MIN_SLEEP_SECONDS,
    ZptCollectorHttpClient,
    assert_collector_base_url,
    collect_remote_listings,
    configured_base_url,
    configured_collector_token,
)


class Command(BaseCommand):
    help = (
        "Office collector: reads the ZPT listing manifest, fetches public Kaspi "
        "offers, and POSTs normalized batches. Does not use local listings ORM "
        "and does not write to Kaspi."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--listing-id",
            action="append",
            type=int,
            default=[],
            help="ProductKaspiListing id from ZPT. Repeatable. Required. Max 10.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch Kaspi offers but do not POST batches to ZPT.",
        )
        parser.add_argument(
            "--max-offers",
            type=int,
            default=32,
            help="Max offers per product (1..100, default 32).",
        )
        parser.add_argument(
            "--sleep-seconds",
            type=float,
            default=1.0,
            help="Pause between products. Minimum 1.0 second.",
        )
        parser.add_argument(
            "--city-id",
            default=None,
            help="Kaspi city id. Default KASPI_REPRICER_CITY_ID or Almaty.",
        )

    def handle(self, *args, **options):
        listing_ids = list(dict.fromkeys(options["listing_id"]))
        if not listing_ids:
            raise CommandError(
                "Укажите --listing-id. Полный обход всех товаров пока запрещён."
            )
        if len(listing_ids) > MAX_LISTINGS:
            raise CommandError(f"Максимум {MAX_LISTINGS} listings за один запуск.")
        sleep_seconds = options["sleep_seconds"]
        if sleep_seconds < MIN_SLEEP_SECONDS:
            raise CommandError("--sleep-seconds не должен быть меньше 1.0 сек.")
        max_offers = options["max_offers"]
        if not 1 <= max_offers <= 100:
            raise CommandError("--max-offers должен быть от 1 до 100.")

        token = configured_collector_token()
        if not token:
            raise CommandError("KASPI_COMPETITOR_COLLECTOR_TOKEN не задан.")
        base_url = configured_base_url()
        if not base_url:
            raise CommandError("ZPT_KASPI_COLLECTOR_BASE_URL не задан.")
        assert_collector_base_url(base_url, allow_localhost=bool(settings.DEBUG))

        timeout_raw = getattr(settings, "KASPI_REPRICER_TIMEOUT_SECONDS", None) or "10"
        try:
            timeout_seconds = float(timeout_raw)
        except (TypeError, ValueError) as exc:
            raise CommandError("KASPI_REPRICER_TIMEOUT_SECONDS должен быть числом.") from exc
        city_id = options["city_id"] or str(
            getattr(settings, "KASPI_REPRICER_CITY_ID", "") or "750000000"
        )

        source = KaspiPublicOfferSource(
            city_id=city_id,
            timeout_seconds=timeout_seconds,
            max_offers=max_offers,
        )
        client = ZptCollectorHttpClient(
            base_url=base_url,
            token=token,
            timeout_seconds=timeout_seconds,
        )
        self.stdout.write(
            self.style.WARNING(
                f"COLLECTOR listings={len(listing_ids)} dry_run={options['dry_run']} "
                f"city={city_id}."
            )
        )
        results = collect_remote_listings(
            listing_ids=listing_ids,
            source=source,
            client=client,
            dry_run=options["dry_run"],
            sleep_seconds=sleep_seconds,
            stdout=self.stdout,
        )
        posted = sum(1 for item in results if item.posted)
        skipped = sum(1 for item in results if item.skipped_reason)
        snapshots = sum(item.snapshots_created for item in results)
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Collector finished: posted={posted}, skipped={skipped}, "
                f"snapshots={snapshots}."
            )
        )
