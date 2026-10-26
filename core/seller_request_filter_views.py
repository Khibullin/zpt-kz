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
    notification_conn = None
    ticket = request.GET.get('notifications', '')
    if token is not None:
        access = find_seller_request_access(token)
        if access is None or access.seller_id is None or access.request_id is None:
            return HttpResponse('Ссылка недействительна.', status=404)
        if access.is_expired():
            return HttpResponse('Срок действия ссылки истёк. Откройте новую заявку или войдите в кабинет.', status=410)
        seller = access.seller
        from core.services.request_connections import enabled
        if ticket:
            from core.services.manual_telegram_invitation import settings_connection
            notification_conn = settings_connection(access, ticket)
            if notification_conn is None:
                return HttpResponse('Ссылка настройки недействительна. Откройте подключение в Telegram заново.', status=403)
        if not notification_conn and enabled() and (not request.user.is_authenticated or seller.user_id != request.user.pk):
            return HttpResponse('Войдите под владельцем организации.', status=403)
        back_url = '../'
    else:
        seller = get_logged_request_seller(request)
        if seller is None:
            return redirect('request_parts_cabinet')
        back_url = reverse('request_parts_cabinet')
    if not seller.is_active:
        return HttpResponse('Настройка фильтра для этой организации недоступна.', status=403)
    error = ''
    initial = initial_filters(seller)
    if request.method == 'POST':
        try:
            data = json.loads(request.POST.get('filters', ''))
            from django.db import transaction
            from core.kazakhstan_locations import KAZAKHSTAN_CITIES
            cities = request.POST.getlist('telegram_cities') if notification_conn and request.POST.get('telegram_all_cities') != 'on' else []
            if notification_conn and request.POST.get('telegram_all_cities') != 'on' and not cities:
                raise ValueError('Выберите города или отметьте «Все города Казахстана».')
            if any(city not in KAZAKHSTAN_CITIES for city in cities):
                raise ValueError('Проверьте выбранные города.')
            with transaction.atomic():
                seller = save_filters(seller, data)
                if notification_conn:
                    from core.models import SellerChannelConnection, SellerChannelConsentEvent
                    conn = SellerChannelConnection.objects.select_for_update().get(pk=notification_conn.pk)
                    if conn.telegram_id != notification_conn.telegram_id:
                        raise ValueError('Подключение изменилось. Откройте настройки в Telegram заново.')
                    granted = request.POST.get('telegram_notifications') == 'on'
                    if granted != conn.telegram_consent:
                        SellerChannelConsentEvent.objects.create(seller=seller, channel='telegram', granted=granted)
                    conn.telegram_consent = granted
                    conn.save(update_fields=['telegram_consent', 'updated_at'])
                    seller.request_filters['telegram_cities'] = list(dict.fromkeys(cities))
                    seller.save(update_fields=['request_filters'])
        except (ValueError, TypeError) as exc:
            error = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else 'Проверьте настройки.'
        else:
            # POST/Redirect/GET: refresh never resubmits profile changes.
            from urllib.parse import urlencode
            response = HttpResponseRedirect('?' + urlencode({'saved': '1', **({'notifications': ticket} if ticket else {})}))
            response['Referrer-Policy'] = 'same-origin'
            return response
    from core.kazakhstan_locations import KAZAKHSTAN_CITIES
    selected_cities = (seller.request_filters or {}).get('telegram_cities', [])
    response = render(request, 'core/seller_request_filters.html', {
        'seller': seller, 'back_url': back_url, 'error': error,
        'notification_conn': notification_conn,
        'notification_all_cities': not selected_cities,
        'notification_cities': [(city, city in selected_cities) for city in KAZAKHSTAN_CITIES],
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
    response.disable_analytics = True
    response['Referrer-Policy'] = 'same-origin'
    return response
