"""Safely import verified AG Parts product links copied from Kaspi Pay.

CSV headings: master_sku,public_url
Default is dry-run; --apply writes only AG Parts' existing Kaspi listings.
This command does not infer ownership from public Kaspi pages or SKU numbers.
"""
import csv
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from catalog.kaspi_public_url import validate_kaspi_public_url
from catalog.models import ProductKaspiListing


class Command(BaseCommand):
    help = "Preview or import AG Parts' verified Kaspi Pay product URLs."

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=Path)
        parser.add_argument("--apply", action="store_true", help="Save validated links.")
        parser.add_argument("--replace-existing", action="store_true", help="Permit replacing an existing nonempty link after manual review.")
        parser.add_argument(
            "--seller-slug", default="ag-parts",
            help="SellerProfile slug (default: ag-parts).",
        )

    def handle(self, *args, **options):
        path = options["csv_file"]
        if not path.is_file():
            raise CommandError(f"CSV file not found: {path}")
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                if not {"master_sku", "public_url"}.issubset(reader.fieldnames or []):
                    raise CommandError("CSV needs master_sku,public_url headers.")
                rows = list(reader)
        except UnicodeError as exc:
            raise CommandError("CSV must be UTF-8.") from exc
        if not rows:
            raise CommandError("CSV is empty.")

        seen = set()
        changes = []
        errors = []
        for row_no, row in enumerate(rows, 2):
            sku = str(row.get("master_sku") or "").strip()
            url = str(row.get("public_url") or "").strip()
            if not sku or not url or sku in seen:
                errors.append(f"row {row_no}: empty or duplicate SKU/URL")
                continue
            seen.add(sku)
            try:
                validate_kaspi_public_url(url)
            except (ValidationError, ValueError) as exc:
                errors.append(f"row {row_no}: invalid Kaspi product URL ({exc})")
                continue
            listings = list(ProductKaspiListing.objects.filter(
                master_sku=sku,
                product__seller_profile__slug=options["seller_slug"],
            ).select_related("product"))
            if len(listings) != 1:
                errors.append(f"row {row_no}: {sku}: expected one AG Parts listing, found {len(listings)}")
                continue
            listing = listings[0]
            if not listing.is_active or listing.product.status != "active":
                errors.append(f"row {row_no}: {sku}: inactive listing or hidden product")
                continue
            if listing.public_url and listing.public_url != url and not options["replace_existing"]:
                errors.append(f"row {row_no}: {sku}: existing URL differs; use --replace-existing only after verification")
                continue
            changes.append((listing, url))
        if errors:
            for error in errors:
                self.stderr.write(error)
            raise CommandError(f"{len(errors)} errors; no changes applied.")

        for listing, url in changes:
            state = "unchanged" if listing.public_url == url else "update"
            self.stdout.write(f"{state}: {listing.master_sku} / product {listing.product_id} -> {url}")

        to_update = [(listing, url) for listing, url in changes if listing.public_url != url]
        if options["apply"]:
            with transaction.atomic():
                for listing, url in to_update:
                    updated = ProductKaspiListing.objects.filter(
                        pk=listing.pk, public_url=listing.public_url
                    ).update(public_url=url, public_url_source="kaspi_pay_copy", public_url_verified_at=timezone.now())
                    if updated != 1:
                        raise CommandError("Concurrent update detected; rolled back.")
            self.stdout.write(self.style.SUCCESS(f"Updated {len(to_update)} AG Parts links."))
        else:
            self.stdout.write(self.style.WARNING(
                f"DRY RUN: {len(to_update)} changes. Copy verified links from Kaspi Pay; "
                "review the preview, then rerun with --apply."
            ))
