from django import template

from catalog.seller_initials import seller_initials as build_seller_initials

register = template.Library()


@register.filter
def seller_initials(value):
    return build_seller_initials(value)


@register.inclusion_tag('catalog/includes/seller_avatar.html')
def seller_avatar(seller, size='lg', link='', wrapper_class='', title='', bare=False):
    if not title and link and seller:
        title = f'Профиль продавца {seller.name}'
    return {
        'seller': seller,
        'size': size,
        'link': link,
        'wrapper_class': wrapper_class,
        'title': title,
        'bare': bare,
    }
