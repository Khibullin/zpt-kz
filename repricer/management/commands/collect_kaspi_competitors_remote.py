from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from integrations.kaspi_competitors import KaspiPublicOfferSource
from repricer.cities import KASPI_KZ_CITIES, validate_city_id
from repricer.collector_remote import (
    MAX_LISTINGS,
    MIN_SLEEP_SECONDS,
    SOURCE_ERROR_REASONS,
    CollectScanResult,
    CollectorRemoteError,
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
        "and does not write to Kaspi. --all-active scans every active listing "
        "in batches of at most 10 Kaspi requests."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--listing-id",
            action="append",
            type=int,
            default=[],
            help="ProductKaspiListing id from ZPT. Repeatable.",
        )
        parser.add_argument(
            "--all-active",
            action="store_true",
            help="Collect every active listing from the production manifest.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=MAX_LISTINGS,
            help="Kaspi requests per internal batch. Max 10, default 10.",
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
            help="Pause between Kaspi requests. Minimum 1.0 second.",
        )
        parser.add_argument(
            "--city-id",
            default=None,
            help="Kaspi city id. Default KASPI_REPRICER_CITY_ID or Almaty.",
        )

        parser.add_argument(
            "--all-cities", action="store_true",
            help="Проверять все населённые пункты из официальной таблицы городов Kaspi, требуется --all-active.",
        )

    def handle(self, *args, **options):
        listing_ids = list(dict.fromkeys(options["listing_id"]))
        if options["all_cities"] and options["city_id"]:
            raise CommandError("--all-cities нельзя совмещать с --city-id.")
        if options["all_cities"] and not options["all_active"]:
            raise CommandError("--all-cities требует --all-active.")
        all_active = bool(options["all_active"])
        if all_active and listing_ids:
            raise CommandError("Нельзя одновременно указывать --all-active и --listing-id.")
        if not all_active and not listing_ids:
            raise CommandError(
                "Укажите --listing-id или --all-active."
            )
        batch_size = options["batch_size"]
        if not 1 <= batch_size <= MAX_LISTINGS:
            raise CommandError(f"--batch-size должен быть от 1 до {MAX_LISTINGS}.")
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

        cities = (
            list(KASPI_KZ_CITIES) if options["all_cities"]
            else [(city_id, dict(KASPI_KZ_CITIES).get(city_id, city_id))]
        )
        for code, _name in cities:
            try:
                validate_city_id(code)
            except ValueError as exc:
                raise CommandError(f"Неизвестный код города Kaspi: {code}") from exc

        mode = "all-active" if all_active else f"ids={len(listing_ids)}"
        scans: list[CollectScanResult] = []
        for code, city_name in cities:
            source = KaspiPublicOfferSource(
                city_id=code, timeout_seconds=timeout_seconds,
                max_offers=max_offers,
            )
            client = ZptCollectorHttpClient(
                base_url=base_url, token=token,
                timeout_seconds=timeout_seconds, city_id=code,
            )
            self.stdout.write(
                self.style.WARNING(
                    f"COLLECTOR {mode} dry_run={options['dry_run']} "
                    f"batch_size={batch_size} city={city_name} ({code})."
                )
            )
            try:
                city_scan = collect_remote_listings(
                    listing_ids=listing_ids, source=source, client=client,
                    dry_run=options["dry_run"], sleep_seconds=sleep_seconds,
                    stdout=self.stdout, all_active=all_active,
                    batch_size=batch_size,
                )
            except CollectorRemoteError as exc:
                raise CommandError(str(exc)) from exc
            scans.append(city_scan)
            if city_scan.aborted:
                self.stderr.write(
                    self.style.WARNING(
                        f"Сбор остановлен в городе {city_name}: {city_scan.abort_reason}"
                    )
                )
                break
        scan = scans[-1]
        results = [row for item in scans for row in item.listings]
        active = len(results)
        unresolved = sum(1 for item in results if item.skipped_reason == "unresolved_mapping")
        collectable = active - unresolved - sum(
            1 for item in results if item.skipped_reason == "not in manifest"
        )
        no_offers = sum(1 for item in results if item.skipped_reason == "no_offers")
        source_errors = sum(
            1 for item in results if item.skipped_reason in SOURCE_ERROR_REASONS
        )
        kaspi_success = sum(1 for item in results if item.offers_received > 0)
        with_competitors = sum(1 for item in results if item.competitor_offers > 0)
        own_only = sum(1 for item in results if item.own_only)
        posted = sum(1 for item in results if item.posted)
        snapshots = sum(item.snapshots_created for item in results)
        recommendations = sum(1 for item in results if item.recommended_price is not None)
        own_ids_label = ", ".join(scan.own_merchant_ids) or "—"
        self.stdout.write("")
        self.stdout.write(f"own_merchant_ids = {own_ids_label}")
        self.stdout.write(f"undercut_amount = {scan.undercut_amount}")
        self.stdout.write(f"active listings = {active}")
        self.stdout.write(f"collectable = {collectable}")
        self.stdout.write(f"unresolved mapping = {unresolved}")
        self.stdout.write(f"Kaspi attempted = {sum(item.kaspi_attempted for item in scans)}")
        self.stdout.write(f"Kaspi success = {kaspi_success}")
        self.stdout.write(f"no offers = {no_offers}")
        self.stdout.write(f"source errors = {source_errors}")
        self.stdout.write(f"with competitors = {with_competitors}")
        self.stdout.write(f"own-only = {own_only}")
        self.stdout.write(f"scan aborted = {'YES' if any(item.aborted for item in scans) else 'NO'}")
        self.stdout.write(f"abort reason = {next((item.abort_reason for item in scans if item.aborted), '—')}")
        self.stdout.write(f"POSTs = {posted}")
        self.stdout.write(f"recommendations possible = {recommendations}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Collector finished: posted={posted}, snapshots={snapshots}."
            )
        )
