"""Static part-collage covers for published maintenance kits.

One named JPEG per kit under static/images/kits/. Templates should use
kit_cover_image() / KitView.cover_image_url instead of slug if-chains.
A mapped collage always wins over leftover MaintenanceKit.cover uploads.
"""
from __future__ import annotations

from django.templatetags.static import static

KIT_COLLAGE_STATIC = {
    'komplekt-to-chery-tiggo-7-pro-15t': (
        'images/kits/tiggo-7-pro-15t-three-filter-collage.jpg'
    ),
    'komplekt-to-exeed-txl-16t': 'images/kits/exeed-txl-16t-four-part-collage.jpg',
    'nabor-to-changan-uni-v-15': 'images/kits/uni-v-15-two-filter-collage.jpg',
    'nabor-to-haval-dargo-20-gw4n20': 'images/kits/dargo-20-two-filter-collage.jpg',
    'nabor-to-chery-tiggo-8-pro-16-sqrf4j16a': (
        'images/kits/tiggo-8-pro-16-two-filter-collage.jpg'
    ),
    'nabor-to-chery-tiggo-7-15': 'images/kits/tiggo-7-15-three-filter-collage.jpg',
    'nabor-to-chery-arrizo-8-16-sqrf4j16c': (
        'images/kits/arrizo-8-16-three-filter-collage.jpg'
    ),
    'nabor-to-chery-tiggo-8-15t-sqre4t15c': (
        'images/kits/tiggo-8-15t-sqre4t15c-three-filter-collage.jpg'
    ),
    'nabor-to-chery-tiggo-8-15t-sqre4t15b': (
        'images/kits/tiggo-8-15t-sqre4t15b-four-part-collage.jpg'
    ),
}

KIT_COLLAGE_ALT = {
    'komplekt-to-chery-tiggo-7-pro-15t': (
        'Воздушный, масляный и салонный фильтры комплекта Chery Tiggo 7 Pro 1.5T'
    ),
    'komplekt-to-exeed-txl-16t': (
        'Воздушный, салонный, масляный фильтры и свечи комплекта EXEED TXL 1.6T'
    ),
    'nabor-to-changan-uni-v-15': (
        'Воздушный и салонный фильтры комплекта Changan UNI-V 1.5'
    ),
    'nabor-to-haval-dargo-20-gw4n20': (
        'Воздушный и масляный фильтры комплекта Haval Dargo 2.0'
    ),
    'nabor-to-chery-tiggo-8-pro-16-sqrf4j16a': (
        'Воздушный и салонный фильтры комплекта Chery Tiggo 8 Pro 1.6'
    ),
    'nabor-to-chery-tiggo-7-15': (
        'Воздушный, масляный и салонный фильтры комплекта Chery Tiggo 7 1.5'
    ),
    'nabor-to-chery-arrizo-8-16-sqrf4j16c': (
        'Воздушный, масляный и салонный фильтры комплекта Chery Arrizo 8 1.6'
    ),
    'nabor-to-chery-tiggo-8-15t-sqre4t15c': (
        'Воздушный, масляный и салонный фильтры комплекта Chery Tiggo 8 1.5T SQRE4T15C'
    ),
    'nabor-to-chery-tiggo-8-15t-sqre4t15b': (
        'Воздушный, салонный, масляный фильтры и свечи комплекта Chery Tiggo 8 1.5T SQRE4T15B'
    ),
}


def kit_collage_static_path(slug: str) -> str:
    return KIT_COLLAGE_STATIC.get(slug, '')


def kit_cover_alt(kit) -> str:
    slug = getattr(kit, 'slug', '') or ''
    mapped = KIT_COLLAGE_ALT.get(slug)
    if mapped:
        return mapped
    name = getattr(kit, 'name', '') or 'комплекта ТО'
    return f'Расходники комплекта {name}'


def kit_cover_image(kit) -> tuple[str, str]:
    """Return (url, alt). Empty url means the placeholder should be used."""
    relative = kit_collage_static_path(getattr(kit, 'slug', '') or '')
    if relative:
        return static(relative), kit_cover_alt(kit)
    cover = getattr(kit, 'cover', None)
    if cover:
        try:
            url = cover.url
        except ValueError:
            url = ''
        if url:
            return url, kit_cover_alt(kit)
    return '', ''
