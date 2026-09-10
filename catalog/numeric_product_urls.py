from __future__ import annotations

from django.http import Http404, HttpResponsePermanentRedirect, HttpResponseRedirect
from django.urls import reverse

from .legacy_product_urls import LEGACY_PRODUCT_SLUG_REDIRECTS
from .models import Product


def _public_product_path(product: Product) -> str:
    slug = (product.slug or '').strip()
    if slug:
        public_slug = LEGACY_PRODUCT_SLUG_REDIRECTS.get(slug, slug)
        return reverse('product_detail', kwargs={'slug': public_slug})
    return reverse('product_detail_old', kwargs={'pk': product.pk})


def numeric_product_entry(request, pk):
    """Resolve legacy numeric PK URLs, numeric slugs and exact numeric articles.

    Numeric paths were historically reserved for Product.pk, which made products
    with a fully numeric slug unreachable. Keep existing PK URLs working first,
    then fall back to an exact numeric slug, and finally to an exact article.
    """
    from .views import product_detail

    active = Product.objects.filter(status='active')

    # Preserve the historical /<pk>/ contract. Move it permanently to the
    # public canonical URL when a slug exists.
    by_pk = active.filter(pk=pk).only('pk', 'slug').first()
    if by_pk is not None:
        target = _public_product_path(by_pk)
        current_path = request.path
        if by_pk.slug and target != current_path:
            return HttpResponsePermanentRedirect(target)
        if by_pk.slug:
            # Avoid a self-redirect when a product PK happens to equal its
            # numeric slug.
            return product_detail(request, slug=by_pk.slug)
        return product_detail(request, pk=by_pk.pk)

    # Keep the raw token from the URL so leading zeroes are not lost by the
    # <int:pk> converter.
    numeric_token = request.path.rstrip('/').rsplit('/', 1)[-1]

    # A product is allowed to have a numeric slug. Serve it directly instead
    # of treating the same value only as a database primary key.
    by_slug = active.filter(slug=numeric_token).only('pk', 'slug').first()
    if by_slug is not None:
        return product_detail(request, slug=numeric_token)

    # A pasted numeric OEM/article should still find the product. Exact match
    # only: fuzzy matching belongs to catalog search, not URL routing.
    article_matches = list(
        active.filter(article=numeric_token).only('pk', 'slug', 'article')[:2]
    )
    if len(article_matches) == 1:
        product = article_matches[0]
        target = _public_product_path(product)
        if target == request.path:
            if product.slug:
                return product_detail(request, slug=product.slug)
            return product_detail(request, pk=product.pk)
        return HttpResponsePermanentRedirect(target)

    if len(article_matches) > 1:
        # Do not make a permanent choice while several sellers/products share
        # the same article. Send the visitor to catalog search instead.
        return HttpResponseRedirect(f'/?q={numeric_token}')

    raise Http404('Product not found')
