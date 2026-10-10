"""Read-only Kaspi public-card ID candidate report.

Some imported master_sku values look like '<numeric>_<numeric>'. The leading
number can match a public Kaspi card ID, but is NOT proof of the actual listing
or its seller. Export for verification; never generate a buy URL or write DB.
"""
import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from catalog.models import ProductKaspiListing


def candidate_public_id(master_sku):
    parts = str(master_sku or "").split("_")
    if len(parts) != 2:
        return ""
    left, right = parts
    if not left.isascii() or not right.isascii():
        return ""
    if left.isdigit() and right.isdigit() and len(left) >= 8:
        return left
    return ""


class Command(BaseCommand):
    help = "Export unverified Kaspi public ID candidates from composite listing SKUs."

    def add_arguments(self, parser):
        parser.add_argument("output", type=Path)

    def handle(self, *args, **options):
        path = options["output"]
        if path.exists():
            raise CommandError(f"Output already exists: {path}")
        qs = ProductKaspiListing.objects.filter(
            is_active=True, public_url="",
            product__seller_profile__slug="ag-parts", product__status="active",
        ).select_related("product").order_by("product__article", "master_sku")
        count = 0
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            w = csv.writer(stream)
            w.writerow(["master_sku", "article", "product_title", "candidate_public_id",
                        "status", "verified_public_url"])
            for item in qs:
                candidate = candidate_public_id(item.master_sku)
                if not candidate:
                    continue
                w.writerow([item.master_sku, item.product.article, item.product.title,
                            candidate, "UNVERIFIED_NEEDS_SELLER_CHECK", ""])
                count += 1
        self.stdout.write(f"Exported {count} unverified candidates. No Kaspi URLs were saved.")
