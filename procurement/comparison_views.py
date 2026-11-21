"""Manager-only, explicit, rate-limited comparison processing.

One item per POST avoids request timeouts. The browser may call it 50 times,
and each item's source results/checkpoint are committed independently.
"""
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from .views import access
from .comparison_enrichment import batch


@access('sales', 'admin')
@require_POST
def run_one_comparison(request):
    source = request.POST.get('apify_source', '')[:255].strip()
    return JsonResponse(batch(limit=1, source_filename=source))
