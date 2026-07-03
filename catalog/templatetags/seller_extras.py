from django import template

register = template.Library()


@register.inclusion_tag('catalog/includes/seller_avatar.html')
def seller_avatar(seller, size='lg', link='', wrapper_class='', title=''):
    if not title and link and seller:
        title = f'Профиль продавца {seller.name}'
    return {
        'seller': seller,
        'size': size,
        'link': link,
        'wrapper_class': wrapper_class,
        'title': title,
    }
