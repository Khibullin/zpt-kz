"""Public, editor-approved SEO knowledge pages for ZPT."""
from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from core.models import EditorialPage
from catalog.fitment_slug_redirects import public_product_slug


@require_GET
def editorial_detail(request, slug):
    if not getattr(settings, 'SEO_EDITORIAL_ENABLED', False):
        raise Http404
    article = get_object_or_404(
        EditorialPage,
        slug=slug,
        status=EditorialPage.STATUS_PUBLISHED,
    )
    products = article.related_products.filter(status='active').exclude(slug='').order_by('pk')[:8]
    related_products = [
        {'product': product, 'url': f'/{public_product_slug(product.slug)}/'}
        for product in products
    ]
    similar_articles = EditorialPage.objects.filter(status=EditorialPage.STATUS_PUBLISHED).exclude(pk=article.pk).order_by('-published_at', '-pk')[:3]
    return render(request, 'editorial/detail.html', {
        'article': article,
        'related_products': related_products,
        'similar_articles': similar_articles,
    })


@require_GET
def editorial_index(request):
    if not getattr(settings, 'SEO_EDITORIAL_ENABLED', False):
        raise Http404
    articles = EditorialPage.objects.filter(
        status=EditorialPage.STATUS_PUBLISHED,
    ).order_by('-published_at', '-pk')
    return render(request, 'editorial/index.html', {'articles': articles})
