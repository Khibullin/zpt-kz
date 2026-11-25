"""Preserve published AG Parts preview URLs on the unified storefront."""
from django.http import Http404, HttpResponsePermanentRedirect
from django.views.decorators.http import require_safe

FILTER_NAMES = {'cabin': 'салонный фильтр', 'air': 'воздушный фильтр', 'oil': 'масляный фильтр', 'spark': 'свеча'}


@require_safe
def filter_category(request, category):
    if category not in FILTER_NAMES:
        raise Http404
    from catalog.storefront import storefront
    params = request.GET.copy()
    params['q'] = FILTER_NAMES[category]
    request.GET = params
    return storefront(request)


@require_safe
def preview_redirect(request, lang, section='', slug=''):
    if lang not in {'ru', 'kk', 'zh'}:
        raise Http404
    if section == 'product':
        # The old preview uses ag-<ZPT id>, never match on similar names.
        if not slug.startswith('ag-') or not slug[3:].isdigit():
            raise Http404
        from catalog.models import Product
        from catalog.templatetags.product_extras import public_product_url
        product = Product.objects.filter(pk=int(slug[3:]), status='active').first()
        if not product:
            raise Http404
        target = public_product_url(product)
    elif section == 'category':
        if slug not in FILTER_NAMES:
            raise Http404
        target = f'/filters/{slug}/'
    else:
        target = {'': '/', 'catalog': '/', 'selection': '/', 'about': '/contacts/', 'contacts': '/contacts/', 'wholesale': '/seller/ag-parts/wholesale/'}.get(section)
        if target is None:
            raise Http404
    return HttpResponsePermanentRedirect('https://agparts.kz' + target)
