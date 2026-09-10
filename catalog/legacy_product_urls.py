from django.shortcuts import redirect
from django.urls import path


LEGACY_PRODUCT_SLUG_REDIRECTS = {
    'audi': 'peugeot-308-hu71151x',
    'chery': 'chery-tiggo-7-t151109111',
    'byd': 'byd-tang-13033898-00',
    'byd-2': 'byd-em2e-8121211e',
    'changan': 'changan-uni-k-1109190cr01',
    'changan-2': 'changan-cs35-s1010140400',
    'changan-3': 'changan-uni-v-c281f2801032601',
    'changan-4': 'changan-s111f2801031700',
    'prado-95': 'prado-95-pnh95',
    'chevrolet': 'chevrolet-brake-pads-2045',
    'jac': 'great-wall-poer-1017110xed95',
    'nissan': 'nissan-304887',
    'jeep': 'jeep-103373',
    'jeep-2': 'jeep-crab-043',
    'jeep-3': 'jeep-crab-056',
    'product': 'japanparts-pp-998af',
    'jeep-4': 'jeep-0201-ja60r',
    'moog': 'moog-k80604',
    'jeep-5': 'jeep-2025-libr',
    'jeep-6': 'jeep-52124302ac',
    'jeep-7': 'jeep-5143700aa',
    'hyundai': 'hyundai-oe18846-11070',
}


def legacy_product_redirect(request, new_slug):
    """Permanently move a weak legacy public URL to its canonical SEO URL."""
    return redirect('product_detail', slug=new_slug, permanent=True)


def canonical_product_alias(request, stored_slug, new_slug):
    """Serve the canonical URL even while the database still stores the old slug.

    This keeps the rollout zero-downtime on Render, where Django migrations are
    not part of the current build command. If the database is migrated later,
    the same route automatically starts using the new stored slug.
    """
    from .models import Product
    from .views import product_detail

    effective_slug = stored_slug
    if Product.objects.filter(status='active', slug=new_slug).exists():
        effective_slug = new_slug

    return product_detail(request, slug=effective_slug)


legacy_product_urlpatterns = []
for old_slug, new_slug in LEGACY_PRODUCT_SLUG_REDIRECTS.items():
    legacy_product_urlpatterns.extend([
        path(
            f'{old_slug}/',
            legacy_product_redirect,
            {'new_slug': new_slug},
        ),
        path(
            f'{new_slug}/',
            canonical_product_alias,
            {'stored_slug': old_slug, 'new_slug': new_slug},
        ),
    ])
