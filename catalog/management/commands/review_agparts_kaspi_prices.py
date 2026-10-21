"""Manual preview/apply of the owner's Kaspi-primary retail price policy.

The data source is the existing, already collected offer snapshots.
NO network, scheduler, Kaspi writes, or price writes in default preview.
"""
from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from catalog.agparts_kaspi_primary import (
    PriceReconciliationError, READY,
    apply_previewed_prices, fingerprint, price_review,
)


class Command(BaseCommand):
    help = (
        "Preview AG Parts ZPT retail price reconciliation against fresh own Kaspi "
        "offers. Price updates require --apply, the exact preview hash and a "
        "separately enabled server-side environment flag."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Apply ONLY eligible prices atomically (disabled by default)")
        parser.add_argument("--expected-hash", default="", help="Exact SHA256 from a fresh preview")

    def handle(self, *args, **options):
        try:
            if options["apply"]:
                if not options["expected_hash"]:
                    raise CommandError("--apply requires --expected-hash from a fresh preview")
                changed = apply_previewed_prices(expected_hash=options["expected_hash"])
                for row in changed:
                    self.stdout.write(
                        "APPLIED id={} article={} old={} new={} seller=AG Parts listing={} snapshot={}".format(
                            row.product_id, row.article, row.zpt_price, row.kaspi_price,
                            row.listing_id, row.captured_at,
                        )
                    )
                self.stdout.write(self.style.SUCCESS("Applied {} price updates; all other prices untouched.".format(len(changed))))
                return

            result = price_review()
            counts = Counter(item.status for item in result)
            self.stdout.write("AG Parts Kaspi PRIMARY price review — READ ONLY")
            for d in result:
                self.stdout.write(
                    "{} | {} | ZPT={} | our_Kaspi={} | min_wholesale={} | {} | listing={} | captured={}".format(
                        d.article, d.status, d.zpt_price, d.kaspi_price,
                        d.wholesale_floor, d.reason, d.listing_id, d.captured_at
                    )
                )
            self.stdout.write("SUMMARY " + ", ".join("{}={}".format(k, v) for k, v in sorted(counts.items())))
            self.stdout.write("PREVIEW_SHA256 " + fingerprint(result))
            self.stdout.write(
                "Price writes are OFF by default. F4J163707010 is blocked pending separate 4-piece "
                "sale-unit/stock conversion. No further action was executed."
            )
        except PriceReconciliationError as exc:
            raise CommandError(str(exc)) from exc
