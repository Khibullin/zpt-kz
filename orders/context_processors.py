from orders.cart import CartManager
from orders.constants import SESSION_CART_KEY


def cart_count(request):
    # The office uses Selection, not a retail basket. Rendering navigation must
    # neither query CartItem nor merge a retail cart into an office session.
    from core.office_privacy import is_private_request
    if is_private_request(request):
        return {'cart_count': 0}
    if not getattr(request.user, 'is_authenticated', False):
        # Merely reading the catalogue must not create a session for each bot
        # or first-time visitor. Cart writes still go through CartManager.
        return {'cart_count': sum(request.session.get(SESSION_CART_KEY, {}).values())}
    return {'cart_count': CartManager(request).get_count()}
