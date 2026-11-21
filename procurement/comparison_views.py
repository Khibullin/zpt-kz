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
    position = request.POST.get('retry_position', '').strip()
    if position:
        try:
            rank = int(position)
        except (TypeError, ValueError):
            return JsonResponse({'error':'Неверная позиция товара'}, status=400)
        if not 0 <= rank < 50:
            return JsonResponse({'error':'Разрешены только первые 50 товаров'}, status=400)
        return JsonResponse(batch(limit=1, force=True,
                                  source_filename=source, position=rank))
    return JsonResponse(batch(limit=1, source_filename=source))
