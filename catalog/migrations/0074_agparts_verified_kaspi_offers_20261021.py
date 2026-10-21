"""Persist manually observed own offers; no product prices or warehouse writes.

Frozen public evidence. Existing office collector includes all active listings,
so newly reconciled URLs participate in its existing read-only manifest.
"""
import json
from datetime import datetime
from pathlib import Path
from django.db import migrations


def import_review(apps, schema_editor):
    alias = schema_editor.connection.alias
    Product = apps.get_model("catalog", "Product")
    Listing = apps.get_model("catalog", "ProductKaspiListing")
    Snapshot = apps.get_model("repricer", "KaspiCompetitorOfferSnapshot")
    records = json.loads((Path(__file__).parent.parent / "data" / "agparts_kaspi_verified_20261021.json").read_text())["offers"]
    for article, row in records.items():
        product = Product.objects.using(alias).filter(pk=row["product_id"], article=article,
            seller_profile__slug="ag-parts", status="active").first()
        if product is None:
            continue  # Test databases / other installations contain no owner products.
        observed = datetime.fromisoformat(row["checked_at"].replace("Z", "+00:00"))
        listing, created = Listing.objects.using(alias).get_or_create(
            product_id=product.pk, master_sku=row["master_sku"],
            defaults={"merchant_sku": article, "is_active": True, "publish_to_kaspi": False})
        if listing.merchant_sku.strip().casefold() != article.casefold():
            raise ValueError("Reviewed Kaspi listing has conflicting merchant article")
        # Preserve a newer manual reconciliation and all export/stock flags.
        if listing.public_url_verified_at is None or listing.public_url_verified_at <= observed:
            Listing.objects.using(alias).filter(pk=listing.pk).update(
                public_url=row["public_url"], public_url_source="manual_browser_review",
                public_url_verified_at=observed)
        Snapshot.objects.using(alias).get_or_create(
            listing_id=listing.pk, seller_code="30363568", source="manual_browser_review",
            captured_at=observed, defaults={"seller_name":"AG Parts", "price":row["price"], "is_available":True})


class Migration(migrations.Migration):
    dependencies = [("catalog", "0073_apply_fitment_audit_20261012"), ("repricer", "0002_kaspicompetitoringestbatch")]
    operations = [migrations.RunPython(import_review, migrations.RunPython.noop)]
