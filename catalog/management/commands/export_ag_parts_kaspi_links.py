"""Export current AG Parts Kaspi listings as a CSV checklist for verified seller URLs."""
import csv
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from catalog.models import ProductKaspiListing


class Command(BaseCommand):
    help = "Export active AG Parts Kaspi listings for owner-specific URL verification."

    def add_arguments(self, parser):
        parser.add_argument("output", type=Path)
        parser.add_argument("--missing-only", action="store_true")

    def handle(self, *args, **options):
        output = options["output"]
        if output.exists():
            raise CommandError(f"Output file already exists: {output}")
        qs = ProductKaspiListing.objects.filter(
            product__seller_profile__slug="ag-parts",
            product__status="active",
            is_active=True,
        ).select_related("product").order_by("product__article", "master_sku")
        if options["missing_only"]:
            qs = qs.filter(public_url="")
        rows = list(qs)
        with output.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["master_sku", "public_url", "article", "product_title", "zpt_url", "link_source"])
            for listing in rows:
                p = listing.product
                writer.writerow([
                    listing.master_sku,
                    listing.public_url,
                    p.article,
                    p.title,
                    p.get_absolute_url(),
                    listing.public_url_source,
                ])
        self.stdout.write(self.style.SUCCESS(f"Exported {len(rows)} Kaspi listings to {output}"))
