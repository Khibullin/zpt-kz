"""Kaspi-first public retail display for the owner's AG Parts products.

Other ZPT sellers retain the existing display rules. AG Parts gets a
price only from a fresh offer of our Kaspi merchant, not from competitor
or stale Product.price. No database write and no outbound Kaspi request.
"""
from django import template

from catalog.agparts_kaspi_primary import (
    ALIGNED, BUNDLE_MIGRATION, READY, PriceReconciliationError,
    evaluate_one,
)
from catalog.kaspi_public_url import display_kaspi_public_url


register = template.Library()
CACHE_ATTRIBUTE = "_agparts_verified_kaspi_retail_info"


def get_agparts_verified_retail(product):
    if not product:
        return None
    seller = getattr(product, "seller_profile", None)
    if seller is None or getattr(seller, "slug", "") != "ag-parts":
        return None
    if hasattr(product, CACHE_ATTRIBUTE):
        return getattr(product, CACHE_ATTRIBUTE)

    info = {
        "is_agparts": True,
        "price": None,
        "package_quantity": None,
        "source": "kaspi",
        "kaspi_url": "",
    }
    try:
        decision = evaluate_one(product)
        if not product.price_on_request and decision.status in (ALIGNED, READY, BUNDLE_MIGRATION):
            info["price"] = decision.kaspi_price
            if decision.article.upper() == "F4J163707010":
                # The owner has explicitly approved only a four-plug kit,
                # but no ZPT stock/cart unit conversion. Kaspi is the sole
                # purchase channel; no local basket or stock claim.
                info["package_quantity"] = 4
            selected = [
                listing for listing in product.kaspi_listings.all()
                if listing.pk == decision.listing_id
            ]
            if len(selected) == 1:
                info["kaspi_url"] = display_kaspi_public_url(selected[0].public_url)
    except PriceReconciliationError:
        pass
    setattr(product, CACHE_ATTRIBUTE, info)
    return info


@register.simple_tag
def agparts_kaspi_retail(product):
    return get_agparts_verified_retail(product)
