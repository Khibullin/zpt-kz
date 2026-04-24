from datetime import timedelta
from django.utils import timezone


def seller_requests(request):
    seller_id = request.GET.get('seller_id')
    period = request.GET.get('period', '7d')

    if not seller_id:
        return JsonResponse({'error': 'seller_id required'}, status=400)

    try:
        seller = Seller.objects.get(id=seller_id)
    except Seller.DoesNotExist:
        return JsonResponse({'error': 'seller not found'}, status=404)

    matches = Match.objects.filter(seller=seller).select_related('request')

    now = timezone.now()

    if period == 'today':
        matches = matches.filter(request__created_at__date=now.date())

    elif period == '7d':
        matches = matches.filter(request__created_at__gte=now - timedelta(days=7))

    elif period == '30d':
        matches = matches.filter(request__created_at__gte=now - timedelta(days=30))

    # all = без фильтра

    data = []

    for m in matches.order_by('-request__created_at'):
        r = m.request

        data.append({
            'id': r.id,
            'created_at': r.created_at.strftime('%d.%m.%Y %H:%M'),
            'city': r.city,
            'brand': r.brand,
            'model': r.model,
            'category': r.category,
            'description': r.description,
            'phone': r.phone,
        })

    return JsonResponse({
        'requests': data,
        'count': len(data)
    })
   from datetime import timedelta
from django.utils import timezone


def seller_requests(request):
    seller_id = request.GET.get('seller_id')
    period = request.GET.get('period', '7d')

    if not seller_id:
        return JsonResponse({'error': 'seller_id required'}, status=400)

    try:
        seller = Seller.objects.get(id=seller_id)
    except Seller.DoesNotExist:
        return JsonResponse({'error': 'seller not found'}, status=404)

    matches = Match.objects.filter(seller=seller).select_related('request')

    now = timezone.now()

    if period == 'today':
        matches = matches.filter(request__created_at__date=now.date())

    elif period == '7d':
        matches = matches.filter(request__created_at__gte=now - timedelta(days=7))

    elif period == '30d':
        matches = matches.filter(request__created_at__gte=now - timedelta(days=30))

    data = []

    for m in matches.order_by('-request__created_at'):
        r = m.request

        data.append({
            'id': r.id,
            'created_at': r.created_at.strftime('%d.%m.%Y %H:%M'),
            'city': r.city,
            'brand': r.brand,
            'model': r.model,
            'category': r.category,
            'description': r.description,
            'phone': r.phone,
        })

    return JsonResponse({
        'requests': data,
        'count': len(data)
    })