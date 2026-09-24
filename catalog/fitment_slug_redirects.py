"""Permanent redirects from misleading product slugs to article-based URLs."""

from django.shortcuts import redirect
from django.urls import path


FITMENT_SLUG_REDIRECTS = {
    'chery-tiggo-7-pro-151000079aa-chery-tiggo-7-pro': 'vozdushnyi-filtr-151000079aa',
    'chery-tiggo-7-f4j163707010-chery-tiggo-7': 'svecha-f4j163707010',
    'changan-cs55': 'masljanyi-filtr-4801012010',
    'chery-tiggo-8-2': 'salonnyi-filtr-301001199aa',
}


def public_product_slug(stored_slug: str) -> str:
    """Public URL slug: remap misleading/legacy stored slugs, keep others."""
    if not stored_slug:
        return stored_slug
    mapped = FITMENT_SLUG_REDIRECTS.get(stored_slug)
    if mapped:
        return mapped
    from catalog.legacy_product_urls import LEGACY_PRODUCT_SLUG_REDIRECTS
    return LEGACY_PRODUCT_SLUG_REDIRECTS.get(stored_slug, stored_slug)


def fitment_slug_redirect(request, new_slug):
    return redirect('product_detail', slug=new_slug, permanent=True)


def fitment_canonical_alias(request, stored_slug, new_slug):
    from catalog.models import Product
    from catalog.views import product_detail

    effective_slug = stored_slug
    if Product.objects.filter(status='active', slug=new_slug).exists():
        effective_slug = new_slug
    return product_detail(request, slug=effective_slug)


fitment_slug_urlpatterns = []
for old_slug, new_slug in FITMENT_SLUG_REDIRECTS.items():
    fitment_slug_urlpatterns.extend([
        path(
            f'{old_slug}/',
            fitment_slug_redirect,
            {'new_slug': new_slug},
        ),
        path(
            f'{new_slug}/',
            fitment_canonical_alias,
            {'stored_slug': old_slug, 'new_slug': new_slug},
        ),
    ])
