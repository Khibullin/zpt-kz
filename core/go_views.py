from __future__ import annotations

from urllib.parse import urlencode

from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from catalog.models import SellerProfile

GO_DESTINATIONS = {
    'requests': '/request-parts/cabinet/',
    'add-product': '/market/seller/add/',
    'wholesale': '/market/?offer=wholesale&all=1',
    'sellers': '/parts-sellers/',
    'help': '/zpt-gid/',
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


@require_http_methods(['GET', 'HEAD'])
def product_whatsapp_redirect(request, product_id):
    """Track an anonymous product WhatsApp click, then redirect to wa.me."""
    from catalog.models import Product
    from catalog.templatetags.product_extras import public_product_whatsapp_message
    from core.phone_utils import build_whatsapp_url, normalize_phone_for_whatsapp
    from orders.wholesale_analytics import (
        EVENT_SELLER_WHATSAPP_CLICK,
        track_wholesale_event,
    )

    product = get_object_or_404(
        Product.objects.select_related('seller_profile'),
        pk=product_id,
    )
    seller = product.seller_profile

    if seller is None:
        normalized = normalize_phone_for_whatsapp(product.whatsapp_number)
        if normalized:
            suffix = normalized[-10:]
            matches = list(
                SellerProfile.objects.filter(phone__icontains=suffix).order_by('pk')[:2]
            )
            if len(matches) == 1:
                seller = matches[0]

    phone = seller.phone if seller and seller.phone else product.whatsapp_number
    target = build_whatsapp_url(
        phone,
        public_product_whatsapp_message(product),
    )
    if not target:
        raise Http404()

    if seller is not None:
        surface = str(request.GET.get('src') or '').strip().lower()
        if surface not in {'card', 'detail'}:
            surface = 'unknown'
        track_wholesale_event(
            request,
            EVENT_SELLER_WHATSAPP_CLICK,
            seller,
            product=product,
            metadata={'surface': surface},
        )

    response = HttpResponseRedirect(target)
    response['Cache-Control'] = 'no-store'
    return response
