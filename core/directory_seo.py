"""Search-indexable local directories, built only from published organization records.

This module never modifies organizations, request distribution, or their filters.
Every city landing is available only after at least three public, contactable
organizations are present; no empty SEO pages are created.
"""
from __future__ import annotations

from urllib.parse import urlencode

from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

from core.models import Seller
from service_requests.models import ServiceSeller


SEO_CITY_SLUGS = {
    'almaty': 'Алматы',
    'astana': 'Астана',
    'shymkent': 'Шымкент',
    'aktobe': 'Актобе',
    'karaganda': 'Караганда',
    'taraz': 'Тараз',
    'atyrau': 'Атырау',
    'ust-kamenogorsk': 'Усть-Каменогорск',
    'pavlodar': 'Павлодар',
    'semey': 'Семей',
    'kyzylorda': 'Кызылорда',
    'kostanay': 'Костанай',
    'aktau': 'Актау',
    'uralsk': 'Уральск',
    'petropavlovsk': 'Петропавловск',
    'turkestan': 'Туркестан',
    'kokshetau': 'Кокшетау',
    'taldykorgan': 'Талдыкорган',
    'konaev': 'Конаев',
    'zhezkazgan': 'Жезказган',
}

CITY_PAGE_MIN_ORGANIZATIONS = 3
CITY_PAGE_PROFILE_LIMIT = 30

CITY_ROUTES = {
    'parts': 'seo_parts_city',
    'dismantlers': 'seo_dismantlers_city',
    'sto': 'seo_sto_city',
    'detailing': 'seo_detailing_city',
}

CITY_LABELS = {
    'parts': 'Магазины автозапчастей',
    'dismantlers': 'Авторазборы',
    'sto': 'СТО и ремонт автомобилей',
    'detailing': 'Детейлинг и тюнинг',
}


def public_parts_organizations():
    """Same publication scope as the public parts and dismantler catalogues."""
    return (
        Seller.objects.filter(is_active=True, is_paused=False, is_test_seller=False)
        .filter(Q(sells_new_parts=True) | Q(directory_parts=True) | Q(is_dismantler=True))
        .exclude(name='')
        .exclude(city='')
        .filter(Q(address__gt='') | Q(whatsapp__gt=''))
        .order_by('pk')
    )


def public_service_organizations():
    return (
        ServiceSeller.objects.filter(
            is_active=True,
            is_paused=False,
            is_test_seller=False,
            seller_type__in=('sto', 'detailing'),
        )
        .exclude(name='')
        .exclude(city='')
        .filter(Q(address__gt='') | Q(whatsapp__gt=''))
        .order_by('pk')
    )


def city_organizations(kind: str, city: str):
    if kind == 'parts':
        return public_parts_organizations().filter(
            Q(sells_new_parts=True) | Q(directory_parts=True),
            city=city,
        ).order_by('name', 'pk')
    if kind == 'dismantlers':
        return public_parts_organizations().filter(
            is_dismantler=True, city=city,
        ).order_by('name', 'pk')
    if kind in ('sto', 'detailing'):
        return public_service_organizations().filter(
            seller_type=kind, city=city,
        ).order_by('name', 'pk')
    raise ValueError('Unknown organization kind')


def city_slug(city: str) -> str | None:
    return next((slug for slug, name in SEO_CITY_SLUGS.items() if name == city), None)


def published_city_page_paths():
    """Called by sitemap generation, using exactly the same quality gate as views."""
    for kind, route in CITY_ROUTES.items():
        for slug, city in SEO_CITY_SLUGS.items():
            if city_organizations(kind, city).count() >= CITY_PAGE_MIN_ORGANIZATIONS:
                yield reverse(route, kwargs={'city_slug': slug})


def city_directory(request, city_slug, kind):
    city = SEO_CITY_SLUGS.get(city_slug)
    if city is None:
        raise Http404('Unknown city')
    queryset = city_organizations(kind, city)
    count = queryset.count()
    if count < CITY_PAGE_MIN_ORGANIZATIONS:
        raise Http404('Not enough published organizations for an SEO landing')

    label = CITY_LABELS[kind]
    description = (
        f'{label} в городе {city}: адреса и контакты опубликованных организаций '
        'на ZPT.KZ. Откройте профиль для связи и уточнения доступности услуг или запчастей.'
    )

    if kind == 'parts':
        directory_url = reverse('parts_sellers_catalog_public')
    elif kind == 'dismantlers':
        directory_url = reverse('dismantlers_catalog')
    else:
        directory_url = reverse('services_catalog')

    filters = {'city': city}
    if kind in ('sto', 'detailing'):
        filters['type'] = kind

    return render(request, 'catalog/seo_city_directory.html', {
        'page_title': f'{label} в {city} — адреса и контакты | ZPT.KZ',
        'page_description': description,
        'page_heading': f'{label} в {city}',
        'page_intro': (
            f'В каталоге ZPT.KZ представлены организации города {city}. '
            'Выберите профиль, проверьте адрес и свяжитесь напрямую.'
        ),
        'city_name': city,
        'kind': kind,
        'is_service': kind in ('sto', 'detailing'),
        'count': count,
        'sellers': queryset[:CITY_PAGE_PROFILE_LIMIT],
        'directory_url': directory_url + '?' + urlencode(filters),
    })


def city_parts(request, city_slug):
    return city_directory(request, city_slug, 'parts')


def city_dismantlers(request, city_slug):
    return city_directory(request, city_slug, 'dismantlers')


def city_sto(request, city_slug):
    return city_directory(request, city_slug, 'sto')


def city_detailing(request, city_slug):
    return city_directory(request, city_slug, 'detailing')
