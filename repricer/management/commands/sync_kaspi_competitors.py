from __future__ import annotations

import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from catalog.models import ProductKaspiListing
from integrations.kaspi_competitors import (
    CompetitorPriceSourceError,
    KaspiPublicOfferSource,
)
from repricer.competitor_sync import sync_competitor_offers_for_listing


class Command(BaseCommand):
    help = (
        "Read-only pilot: получает публичные предложения Kaspi и сохраняет "
        "исторические снимки цен. Ничего не меняет в Kaspi."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--listing-id",
            action="append",
            type=int,
            default=[],
            help="ID ProductKaspiListing. Можно повторять. Максимум 10 за запуск.",
        )
        parser.add_argument(
            "--master-sku",
            action="append",
            default=[],
            help="Kaspi master SKU/product id. Можно повторять.",
        )
        parser.add_argument(
            "--city-id",
            default=None,
            help="Kaspi city id. По умолчанию KASPI_REPRICER_CITY_ID или Алматы.",
        )
        parser.add_argument(
            "--max-offers",
            type=int,
            default=32,
            help="Максимум предложений на товар (1..100, по умолчанию 32).",
        )
        parser.add_argument(
            "--sleep-seconds",
            type=float,
            default=1.0,
            help="Пауза между товарами. Минимум 0.5 сек, по умолчанию 1.0.",
        )

    @staticmethod
    def _configured(name: str, default: str) -> str:
        value = getattr(settings, name, None)
        if value is None:
            value = os.getenv(name, default)
        return str(value or default).strip()

    def _resolve_listings(self, options):
        listing_ids = list(dict.fromkeys(options["listing_id"]))
        master_skus = [str(value).strip() for value in options["master_sku"] if str(value).strip()]

        if not listing_ids and not master_skus:
            raise CommandError(
                "Для безопасного пилота явно укажите --listing-id или --master-sku "
                "для 5–10 выбранных товаров."
            )

        queryset = ProductKaspiListing.objects.select_related("product").filter(is_active=True)
        selected = {}
        if listing_ids:
            for listing in queryset.filter(pk__in=listing_ids):
                selected[listing.pk] = listing
        if master_skus:
            for listing in queryset.filter(master_sku__in=master_skus):
                selected[listing.pk] = listing

        requested_count = len(set(listing_ids)) + len(set(master_skus))
        if len(selected) == 0:
            raise CommandError("Не найдено ни одного активного Kaspi-листинга для пилота.")
        if len(selected) > 10 or requested_count > 10:
            raise CommandError("Пилот ограничен максимум 10 товарами за один запуск.")

        return sorted(selected.values(), key=lambda item: item.pk)

    def handle(self, *args, **options):
        listings = self._resolve_listings(options)
        max_offers = options["max_offers"]
        sleep_seconds = options["sleep_seconds"]
        if not 1 <= max_offers <= 100:
            raise CommandError("--max-offers должен быть от 1 до 100.")
        if sleep_seconds < 0.5:
            raise CommandError("--sleep-seconds не должен быть меньше 0.5 сек.")

        city_id = options["city_id"] or self._configured(
            "KASPI_REPRICER_CITY_ID",
            "750000000",
        )
        timeout_raw = self._configured("KASPI_REPRICER_TIMEOUT_SECONDS", "10")
        try:
            timeout_seconds = float(timeout_raw)
        except ValueError as exc:
            raise CommandError("KASPI_REPRICER_TIMEOUT_SECONDS должен быть числом.") from exc

        source = KaspiPublicOfferSource(
            city_id=city_id,
            timeout_seconds=timeout_seconds,
            max_offers=max_offers,
        )

        success = 0
        errors = 0
        created = 0
        self.stdout.write(
            self.style.WARNING(
                f"READ-ONLY PILOT: listings={len(listings)}, city={city_id}. "
                "Запись цен в Kaspi отключена."
            )
        )

        for index, listing in enumerate(listings):
            article = listing.product.article or f"product-{listing.product_id}"
            try:
                result = sync_competitor_offers_for_listing(
                    listing=listing,
                    source=source,
                    source_name=source.source_name,
                )
            except CompetitorPriceSourceError as exc:
                errors += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"{article} | master_sku={listing.master_sku}: {exc}"
                    )
                )
            else:
                success += 1
                created += result.snapshots_created
                self.stdout.write(
                    f"{article} | master_sku={listing.master_sku} | "
                    f"offers={result.offers_received} | "
                    f"snapshots={result.snapshots_created}"
                )

            if index < len(listings) - 1:
                time.sleep(sleep_seconds)

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Пилот завершён: успешно={success}, ошибок={errors}, "
                f"снимков цен={created}."
            )
        )
        self.stdout.write(
            self.style.WARNING(
                "Данные только собраны. Для расчёта рекомендаций сначала укажите "
                "KASPI_OWN_MERCHANT_IDS или KASPI_OWN_MERCHANT_NAMES, чтобы наша "
                "собственная цена не считалась конкурентом."
            )
        )
