from __future__ import annotations

from urllib.parse import urlencode

from django.http import Http404, HttpResponseRedirect
from django.views.decorators.http import require_http_methods

from catalog.models import SellerProfile

GO_DESTINATIONS = {
    'requests': '/request-parts/cabinet/',
    'add-product': '/market/seller/add/',
    'wholesale': '/market/?offer=wholesale&all=1',
    'sellers': '/parts-sellers/',
    'help': '/request-parts/help/',
}

# catalog.urls is mounted at both /market/ and /. reverse() resolves to
# /seller/login/ and /seller/add/, so WhatsApp go-links keep the /market/ paths.
MARKET_SELLER_LOGIN = '/market/seller/login/'
MARKET_ADD_PRODUCT = GO_DESTINATIONS['add-product']


def _authenticated_user_has_seller_profile(request) -> bool:
    user = getattr(request, 'user', None)
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    user_id = getattr(user, 'pk', None)
    if not user_id:
        return False
    return SellerProfile.objects.filter(user_id=user_id).exists()


def _add_product_target(request) -> str:
    if _authenticated_user_has_seller_profile(request):
        return MARKET_ADD_PRODUCT
    query = urlencode({'next': MARKET_ADD_PRODUCT})
    return f'{MARKET_SELLER_LOGIN}?{query}'


@require_http_methods(['GET', 'HEAD'])
def go_redirect(request, destination):
    if destination == 'add-product':
        target = _add_product_target(request)
    else:
        target = GO_DESTINATIONS.get(destination)
        if target is None:
            raise Http404()
    response = HttpResponseRedirect(target)
    response['Cache-Control'] = 'no-store'
    return response
