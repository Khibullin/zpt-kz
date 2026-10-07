"""Публичная карточка заявки для перехода из Instagram.

Страница показывает только безопасные поля и только ту заявку, которая уже
явно допущена к публичному Instagram-представлению. Телефон, VIN и секретные
ссылки продавца сюда не попадают.
"""

from __future__ import annotations

from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

from catalog.instagram_public_text import build_public_part_request
from catalog.instagram_service import PUBLIC_REQUEST_STATUSES
from core.models import InstagramPublication, Request
from core.services.seller_identity import get_logged_request_seller

PUBLIC_PAGE_PUBLICATION_STATUSES = frozenset({
    InstagramPublication.STATUS_APPROVED,
    InstagramPublication.STATUS_QUEUED,
    InstagramPublication.STATUS_PUBLISHING,
    InstagramPublication.STATUS_PUBLISHED,
})


def request_is_explicitly_public(product_request) -> bool:
    """Заявка открыта по публичному URL только после допуска в Instagram."""
    if product_request is None or product_request.pk is None:
        return False
    status = (product_request.status or '').strip().lower()
    if status not in PUBLIC_REQUEST_STATUSES:
        return False
    return InstagramPublication.objects.filter(
        request_id=product_request.pk,
        status__in=PUBLIC_PAGE_PUBLICATION_STATUSES,
    ).exists()


def public_part_request(request, request_id: int):
    product_request = Request.objects.filter(pk=request_id).first()
    if not request_is_explicitly_public(product_request):
        raise Http404

    public = build_public_part_request(product_request)
    seller = get_logged_request_seller(request)
    seller_href = reverse('request_parts_register')
    seller_action = 'Зарегистрироваться и получать заявки'
    if seller is not None:
        seller_href = reverse('request_parts_cabinet')
        seller_action = 'Перейти в кабинет продавца'

    photos = []
    for photo in product_request.photos.all()[:6]:
        image = getattr(photo, 'image', None)
        if image and getattr(image, 'name', ''):
            photos.append(image.url)

    return render(
        request,
        'core/public_part_request.html',
        {
            'public_request': public,
            'photos': photos,
            'seller_href': seller_href,
            'seller_action': seller_action,
            'seller_is_logged_in': seller is not None,
            'buyer_href': reverse('request_parts_form'),
        },
    )
