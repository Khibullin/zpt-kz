"""Record a manually inspected Kaspi public product card for AG Parts.

Evidence: merchant cabinet SKU 423766246 links to public product 138020172,
user provided screenshots showing AG Parts as the only listed seller and
matching article/price. This does not guarantee future seller exclusivity.
"""
from django.db import migrations
from django.utils import timezone

SKU = "423766246"
ARTICLE = "CD569F2801032700"
URL = "https://kaspi.kz/shop/p/salonnyi-fil-tr-cd569f2801032700-138020172/"


def add_confirmed_link(apps, schema_editor):
    Listing = apps.get_model("catalog", "ProductKaspiListing")
    db = schema_editor.connection.alias
    candidates = list(Listing.objects.using(db).filter(
        master_sku=SKU,
        merchant_sku=ARTICLE,
        product__article=ARTICLE,
        product__seller_profile__slug="ag-parts",
        is_active=True,
    ))
    if len(candidates) != 1:
        raise RuntimeError("Cannot uniquely identify confirmed AG Parts Kaspi listing")
    listing = candidates[0]
    if listing.public_url and listing.public_url != URL:
        raise RuntimeError("Existing different Kaspi URL requires reconciliation")
    if listing.public_url == URL and listing.public_url_source == "public_seller_checked":
        return
    Listing.objects.using(db).filter(pk=listing.pk).update(
        public_url=URL,
        public_url_source="public_seller_checked",
        public_url_verified_at=timezone.now(),
    )


class Migration(migrations.Migration):
    dependencies = [("catalog", "0060_kaspi_url_provenance")]
    operations = [migrations.RunPython(add_confirmed_link, migrations.RunPython.noop)]
