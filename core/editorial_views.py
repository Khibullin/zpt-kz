"""Public, editor-approved SEO knowledge pages for ZPT."""
from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from core.models import EditorialPage


@require_GET
def editorial_detail(request, slug):
    if not getattr(settings, 'SEO_EDITORIAL_ENABLED', False):
        raise Http404
    article = get_object_or_404(
        EditorialPage,
        slug=slug,
        status=EditorialPage.STATUS_PUBLISHED,
    )
    return render(request, 'editorial/detail.html', {'article': article})


@require_GET
def editorial_index(request):
    if not getattr(settings, 'SEO_EDITORIAL_ENABLED', False):
        raise Http404
    articles = EditorialPage.objects.filter(
        status=EditorialPage.STATUS_PUBLISHED,
    ).order_by('-published_at', '-pk')
    return render(request, 'editorial/index.html', {'articles': articles})
