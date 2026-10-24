"""Public Kaspi purchase links: never guess a URL from SKU or article."""
from django import template

from catalog.kaspi_public_url import display_kaspi_public_url
from catalog.templatetags.agparts_primary_price import get_agparts_verified_retail

register = template.Library()


@register.filter
def kaspi_buy_url(product):
    """Only a verified URL of an active, published listing of our product."""
    if not product:
        return ""
    # AG Parts: do not link a stale/unpriced offer, or a single-plug
    # F4J163707010 product. Other ZPT sellers keep their old logic.
    offer = get_agparts_verified_retail(product)
    if offer is not None:
        return offer["kaspi_url"]
    listings = product.kaspi_listings.all()
    for listing in listings:
        if listing.is_active:
            url = display_kaspi_public_url(listing.public_url)
            if url:
                return url
    return ""
