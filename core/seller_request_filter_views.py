import json
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from core.models import Brand, CarModel, PartCategory
from core.services.seller_identity import get_logged_request_seller
from core.services.seller_request_access import find_seller_request_access
from core.services.seller_request_filters import initial_filters, save_filters, filter_rows


@never_cache
@csrf_protect
@require_http_methods(['GET', 'POST'])
def seller_request_filters(request, token=None):
    if token is not None:
        access = find_seller_request_access(token)
        if access is None or access.seller_id is None or access.request_id is None:
            return HttpResponse('Ссылка недействительна.', status=404)
        if access.is_expired():
            return HttpResponse('Срок действия ссылки истёк. Откройте новую заявку или войдите в кабинет.', status=410)
        seller = access.seller
        back_url = '../'
    else:
        seller = get_logged_request_seller(request)
        if seller is None:
            return redirect('request_parts_cabinet')
        back_url = reverse('request_parts_cabinet')
    if not seller.is_active or seller.is_test_seller:
        return HttpResponse('Настройка фильтра для этой организации недоступна.', status=403)
    error = ''
    initial = initial_filters(seller)
    if request.method == 'POST':
        try:
            data = json.loads(request.POST.get('filters', ''))
            seller = save_filters(seller, data)
        except (ValueError, TypeError) as exc:
            error = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else 'Проверьте настройки.'
        else:
            # POST/Redirect/GET: refresh never resubmits profile changes.
            return HttpResponseRedirect('?saved=1')
    response = render(request, 'core/seller_request_filters.html', {
        'seller': seller, 'back_url': back_url, 'error': error,
        'saved': request.GET.get('saved') == '1' and seller.request_filters is not None,
        'confirmed': seller.request_filters is not None,
        'filter_rows': filter_rows(seller.request_filters),
        'filter_data': initial,
        'filter_catalog': {
            'brands': list(Brand.objects.filter(transport_type__in=['car', 'truck']).order_by('name', 'transport_type', 'pk').values('id', 'name', 'transport_type')),
            'models': list(CarModel.objects.filter(transport_type__in=['car', 'truck']).order_by('name', 'pk').values('id', 'name', 'brand_id')),
            'categories': list(PartCategory.objects.order_by('name').values('id', 'name')),
        },
    }, status=400 if error else 200)
    response['Referrer-Policy'] = 'no-referrer'
    return response
