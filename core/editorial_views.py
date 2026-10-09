"""Public, editor-approved SEO knowledge pages for ZPT."""
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from core.models import EditorialPage


@require_GET
def editorial_detail(request, slug):
    article = get_object_or_404(
        EditorialPage,
        slug=slug,
        status=EditorialPage.STATUS_PUBLISHED,
    )
    return render(request, 'editorial/detail.html', {'article': article})
