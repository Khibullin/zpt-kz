from __future__ import annotations

from django.http import Http404, HttpResponseRedirect
from django.views.decorators.http import require_http_methods

GO_DESTINATIONS = {
    'requests': '/request-parts/cabinet/',
    'add-product': '/market/seller/add/',
    'wholesale': '/market/?offer=wholesale&all=1',
    'sellers': '/parts-sellers/',
    'help': '/request-parts/faq/',
}


@require_http_methods(['GET', 'HEAD'])
def go_redirect(request, destination):
    target = GO_DESTINATIONS.get(destination)
    if target is None:
        raise Http404()
    response = HttpResponseRedirect(target)
    response['Cache-Control'] = 'no-store'
    return response
