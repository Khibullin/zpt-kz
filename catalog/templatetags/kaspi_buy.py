"""Public Kaspi purchase links: never guess a URL from SKU or article."""
from django import template

from catalog.kaspi_public_url import display_kaspi_public_url

register = template.Library()


@register.filter
def kaspi_buy_url(product):
    """Only a verified URL of an active, published listing of our product."""
    if not product or not getattr(product, "publish_to_kaspi", False):
        return ""
    listings = product.kaspi_listings.all()
    for listing in listings:
        if listing.is_active and listing.publish_to_kaspi:
            url = display_kaspi_public_url(listing.public_url)
            if url:
                return url
    return ""
