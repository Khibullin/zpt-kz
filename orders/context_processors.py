from orders.cart import CartManager


def cart_count(request):
    return {
        'cart_count': CartManager(request).get_count(),
    }
