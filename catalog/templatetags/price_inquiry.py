from django import template
from catalog.price_inquiries import can_request_product_price
register = template.Library()
register.filter('can_request_product_price', can_request_product_price)
