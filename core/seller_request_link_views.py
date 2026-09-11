from __future__ import annotations

from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from core.services.seller_request_access import find_seller_request_access

TEMPLATE = 'core/seller_request_link.html'


def _response(request, *, page_state: str, status: int):
    response = render(
        request,
        TEMPLATE,
        {'page_state': page_state},
        status=status,
    )
    response['Cache-Control'] = 'no-store'
    return response


@require_http_methods(['GET', 'HEAD'])
def seller_request_link(request, token):
    access = find_seller_request_access(token)
    if access is None:
        return _response(request, page_state='invalid', status=404)
    if access.is_expired():
        return _response(request, page_state='expired', status=410)
    return _response(request, page_state='valid', status=200)
