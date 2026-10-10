"""Use the AG Parts seller offer context for the four previously checked cards."""
from django.db import migrations
from django.utils import timezone


CHECKED_LISTINGS = (
    ("126807700", "272774M400", "https://kaspi.kz/shop/p/salonnyi-fil-tr-272774m400-138029683/", "https://kaspi.kz/shop/p/salonnyi-fil-tr-272774m400-138029683/?c=750000000&m=30363568&ms=true"),
    ("423766246", "CD569F2801032700", "https://kaspi.kz/shop/p/salonnyi-fil-tr-cd569f2801032700-138020172/", "https://kaspi.kz/shop/p/salonnyi-fil-tr-cd569f2801032700-138020172/?c=750000000&m=30363568&ms=true"),
    ("801748033", "301000265AA", "https://kaspi.kz/shop/p/salonnyi-fil-tr-301000265aa-141508690/", "https://kaspi.kz/shop/p/salonnyi-fil-tr-301000265aa-141508690/?c=750000000&m=30363568&ms=true"),
    ("806873204", "4801012010", "https://kaspi.kz/shop/p/masljanyi-fil-tr-4801012010-139279709/", "https://kaspi.kz/shop/p/masljanyi-fil-tr-4801012010-139279709/?c=750000000&m=30363568&ms=true"),
)


def use_agparts_seller_urls(apps, schema_editor):
    Listing = apps.get_model("catalog", "ProductKaspiListing")
    db = schema_editor.connection.alias

    for master_sku, article, previous_url, seller_url in CHECKED_LISTINGS:
        candidates = list(
            Listing.objects.using(db).filter(
                master_sku=master_sku,
                merchant_sku=article,
                product__article=article,
                product__status="active",
                product__seller_profile__slug="ag-parts",
                is_active=True,
                public_url=previous_url,
                public_url_source="public_seller_checked",
            )
        )
        if not candidates:
            continue
        if len(candidates) != 1:
            raise RuntimeError(
                f"Cannot uniquely identify AG Parts Kaspi listing for {article}"
            )

        Listing.objects.using(db).filter(
            pk=candidates[0].pk,
            public_url=previous_url,
            public_url_source="public_seller_checked",
        ).update(
            public_url=seller_url,
            public_url_verified_at=timezone.now(),
        )


class Migration(migrations.Migration):
    dependencies = [("catalog", "0063_save_verified_agparts_kaspi_offers")]

    operations = [
        migrations.RunPython(use_agparts_seller_urls, migrations.RunPython.noop)
    ]
