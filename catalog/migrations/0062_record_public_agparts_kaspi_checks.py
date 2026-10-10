"""Record public Kaspi cards whose active offers were checked for AG Parts.

For each card, the public seller list showed AG Parts and a merchantSku equal
to the matching active ZPT listing's master_sku. Keep the public product-card
URL already in use; only add verification provenance.
"""
from django.db import migrations
from django.utils import timezone


CHECKED_LISTINGS = (
    (
        "126807700",
        "272774M400",
        "https://kaspi.kz/shop/p/salonnyi-fil-tr-272774m400-138029683/",
    ),
    (
        "801748033",
        "301000265AA",
        "https://kaspi.kz/shop/p/salonnyi-fil-tr-301000265aa-141508690/",
    ),
    (
        "806873204",
        "4801012010",
        "https://kaspi.kz/shop/p/masljanyi-fil-tr-4801012010-139279709/",
    ),
    (
        "423766246",
        "CD569F2801032700",
        "https://kaspi.kz/shop/p/salonnyi-fil-tr-cd569f2801032700-138020172/",
    ),
)


def record_public_seller_checks(apps, schema_editor):
    Listing = apps.get_model("catalog", "ProductKaspiListing")
    db = schema_editor.connection.alias

    for master_sku, article, expected_url in CHECKED_LISTINGS:
        candidates = list(
            Listing.objects.using(db).filter(
                master_sku=master_sku,
                merchant_sku=article,
                product__article=article,
                product__status="active",
                product__seller_profile__slug="ag-parts",
                is_active=True,
            )
        )
        if not candidates:
            # Fresh/test databases may not contain imported production listings.
            continue
        if len(candidates) != 1:
            raise RuntimeError(
                f"Cannot uniquely identify AG Parts Kaspi listing for {article}"
            )

        listing = candidates[0]
        if listing.public_url != expected_url:
            raise RuntimeError(
                f"Existing Kaspi URL differs from the checked card for {article}"
            )

        Listing.objects.using(db).filter(
            pk=listing.pk,
            public_url=expected_url,
        ).update(
            public_url_source="public_seller_checked",
            public_url_verified_at=timezone.now(),
        )


class Migration(migrations.Migration):
    dependencies = [("catalog", "0061_confirm_cd569f2801032700_kaspi_link")]

    operations = [
        migrations.RunPython(record_public_seller_checks, migrations.RunPython.noop)
    ]
