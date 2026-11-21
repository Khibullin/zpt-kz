"""Immutable, unverified Kaspi observations from the 21 Nov 2026 first Apify export.

Only nine SRR-brand mismatches with exact IDs are represented here. These are
review evidence, not approved supplier-product equivalences or live prices.
No KaspiPriceAudit / PermanentBuyingItem is inserted by this module.
"""
from .models import KaspiReviewTask

SOURCE = "dataset_kaspi-api_2026-11-21_13-39-32-482(2).json"

# Article -> (Omega's exact brand, Kaspi product ID, Kaspi title,
#             source card URL, minimum offered price in snapshot, source image)
RAW_SNAPSHOTS = {
    "SRR61246": ("SRR/JAPAN", "143949413", "SRR передний SRR61246 1 шт", "https://kaspi.kz/shop/p/srr-perednii-srr61246-1-sht-143949413/", 16330, "https://resources.cdn-kaspi.kz/img/m/p/pb9/pe3/58074652.jpg?format=preview-large"),
    "SA-160004": ("SRR OE", "179824674", "SRR задний SA-160004 1 шт", "https://kaspi.kz/shop/p/srr-zadnii-sa-160004-1-sht-179824674/", 15016, "https://resources.cdn-kaspi.kz/img/m/p/p22/pd4/188579872.jpg?format=preview-large"),
    "SA-160013": ("SRR OE", "179820654", "SRR задний SA-160013 1 шт", "https://kaspi.kz/shop/p/srr-zadnii-sa-160013-1-sht-179820654/", 14143, "https://resources.cdn-kaspi.kz/img/m/p/pd0/pe1/188574923.jpg?format=preview-large"),
    "SA-160017": ("SRR OE", "143104463", "SRR передний SA-160017 1 шт", "https://kaspi.kz/shop/p/srr-perednii-sa-160017-1-sht-143104463/", 10585, "https://resources.cdn-kaspi.kz/img/m/p/p19/pd2/55090317.jpg?format=preview-large"),
    "SA-160018": ("SRR OE", "179820604", "SRR задний SA-160018 1 шт", "https://kaspi.kz/shop/p/srr-zadnii-sa-160018-1-sht-179820604/", 15240, "https://resources.cdn-kaspi.kz/img/m/p/p1e/pc5/188574870.jpg?format=preview-large"),
    "SA-160032": ("SRR OE", "179237174", "SRR задний SA-160032 1 шт", "https://kaspi.kz/shop/p/srr-zadnii-sa-160032-1-sht-179237174/", 14507, "https://resources.cdn-kaspi.kz/img/m/p/p73/p4e/186559715.jpg?format=preview-large"),
    "SA-160061": ("SRR OE", "144011751", "SRR задний SA-160061 1 шт", "https://kaspi.kz/shop/p/srr-zadnii-sa-160061-1-sht-144011751/", 11865, "https://resources.cdn-kaspi.kz/img/m/p/p9c/p43/58302870.jpg?format=preview-large"),
    "SA-160095": ("SRR OE", "142774669", "Передний SA-160095 1 шт", "https://kaspi.kz/shop/p/perednii-sa-160095-1-sht-142774669/", 27260, "https://resources.cdn-kaspi.kz/img/m/p/p1f/p7f/53950399.jpg?format=preview-large"),
    "SA-160126": ("SRR OE", "143207140", "SRR задний SA-160126 1 шт", "https://kaspi.kz/shop/p/srr-zadnii-sa-160126-1-sht-143207140/", 10836, "https://resources.cdn-kaspi.kz/img/m/p/p65/pea/55492916.jpg?format=preview-large"),
}

# A cheaper ONE-PIECE SRR card with the same complete article occurs in
# the same Apify export for each item below. These are separate Kaspi IDs;
# do not overwrite/merge the originally selected source card or prices.
LOWEST_SAME_ARTICLE = {
    "SA-160013": ("124181139", 11933,
        "https://kaspi.kz/shop/p/srr-zadnii-sa-160013-1-sht-124181139/"),
    "SA-160018": ("129584906", 12330,
        "https://kaspi.kz/shop/p/srr-zadnii-sa-160018-1-sht-129584906/"),
    "SA-160032": ("175147139", 12288,
        "https://kaspi.kz/shop/p/srr-zadnii-sa-160032-1-sht-175147139/"),
    "SA-160095": ("182821049", 23065,
        "https://kaspi.kz/shop/p/srr-perednii-sa-160095-1-sht-182821049/"),
}

def srr_review_snapshot(offer, source_filename):
    """Only return a snapshot for its exact, still-unverified SRR review task."""
    if source_filename != SOURCE or offer is None:
        return None
    row = RAW_SNAPSHOTS.get(str(offer.article).strip().upper())
    if row is None:
        return None
    supplier_brand, product_id, title, url, min_price, image = row
    if str(offer.brand).strip().casefold() != supplier_brand.casefold():
        return None
    if not KaspiReviewTask.objects.filter(
        offer_id=offer.pk,
        status__in=("pending", "blocked"),
        reason__startswith="Apify 21.11.2026:",
        reason__contains="Kaspi ID " + product_id + ".",
    ).exists():
        return None
    cheapest_id, global_min_price, cheapest_url = LOWEST_SAME_ARTICLE.get(
        str(offer.article).strip().upper(), (product_id, min_price, url)
    )
    return {
        "product_id": product_id, "brand": "SRR", "title": title,
        "url": url, "min_price": min_price, "image": image,
        "comparison_min_price": global_min_price,
        "cheapest_product_id": cheapest_id, "cheapest_url": cheapest_url,
        "different_cheapest_card": cheapest_id != product_id,
    }
