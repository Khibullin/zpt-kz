"""Owner-only filter form, accessible from cabinet or a signed personal match link."""
import json
from django.conf import settings
from django.core import signing
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from core.models import Brand, CarModel
from core.phone_utils import build_whatsapp_url, normalize_phone_for_whatsapp
from .models import ServiceMatch
from .services.service_seller_identity import get_current_service_seller, ServiceSellerAccessDenied
from .services.notification_template import SERVICE_LABELS
from .services.request_filters import initial_filters, save_filters, service_options

SALT = 'service-request-owner-link-v1'
MAX_AGE = 72 * 60 * 60


def personal_request_url(req, seller):
    match = ServiceMatch.objects.filter(request=req, seller=seller).order_by('pk').first()
    if match is None:
        return ''
    token = signing.dumps({'match': match.pk}, salt=SALT)
    return str(settings.PUBLIC_BASE_URL).rstrip('/') + reverse('service_personal_request', kwargs={'token': token})


def resolve_match(token):
    try:
        data = signing.loads(token, salt=SALT, max_age=MAX_AGE)
        match = ServiceMatch.objects.select_related('seller', 'request').filter(pk=data['match']).first()
    except signing.SignatureExpired:
        return None, HttpResponse('Срок действия ссылки истёк. Войдите в кабинет.', status=410)
    except (signing.BadSignature, ValueError, TypeError, KeyError):
        match = None
    if match is None:
        return None, HttpResponse('Ссылка недействительна.', status=404)
    if not match.seller.is_active:
        return None, HttpResponse('Доступ к этой организации недоступен.', status=403)
    return match, None


def private_response(response):
    response.disable_analytics = True
    response['Referrer-Policy'] = 'no-referrer'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


@never_cache
@require_http_methods(['GET'])
def personal_request(request, token):
    match, error = resolve_match(token)
    if error is not None:
        return private_response(error)
    req = match.request
    phone = normalize_phone_for_whatsapp(req.phone)
    return private_response(render(request, 'service-request/personal_request.html', {
        'req': req, 'service_type_label': SERVICE_LABELS.get(req.service_type, 'Автосервис'),
        'services': req.services.all(),
        'whatsapp_url': build_whatsapp_url(req.phone), 'phone': '+' + phone if phone else '',
    }))


@never_cache
@csrf_protect
@require_http_methods(['GET', 'POST'])
def request_filters(request, token=None):
    if token is not None:
        match, error = resolve_match(token)
        if error is not None:
            return private_response(error)
        seller = match.seller
        back_url = '../'
    else:
        try:
            seller = get_current_service_seller(request)
        except ServiceSellerAccessDenied as exc:
            if exc.status == 401:
                return redirect('/service-request/cabinet/')
            return private_response(HttpResponse(exc.message, status=exc.status))
        back_url = '/service-request/cabinet/'
    if not seller.is_active:
        return private_response(HttpResponse('Настройка фильтра для этой организации недоступна.', status=403))
    if seller.seller_type not in ('sto', 'detailing'):
        return private_response(HttpResponse('Направление организации не определено.', status=403))
    error = ''
    if request.method == 'POST':
        try:
            seller = save_filters(seller, json.loads(request.POST.get('filters', '')))
        except (ValueError, TypeError) as exc:
            error = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else 'Проверьте настройки.'
        else:
            return private_response(HttpResponseRedirect('?saved=1'))
    sto = seller.seller_type == 'sto'
    return private_response(render(request, 'core/seller_request_filters.html', {
        'seller': seller, 'back_url': back_url, 'error': error,
        'saved': request.GET.get('saved') == '1' and seller.request_filters is not None,
        'confirmed': seller.request_filters is not None,
        'service_filter_label': 'СТО и ремонт' if sto else 'Детейлинг и тюнинг',
        'category_heading': 'Виды ремонтных работ' if sto else 'Услуги детейлинга и тюнинга',
        'category_all_label': 'Все виды работ' if sto else 'Все услуги',
        'category_select_label': 'Выбрать работы' if sto else 'Выбрать услуги',
        'filter_data': initial_filters(seller),
        'filter_catalog': {
            'brands': list(Brand.objects.filter(transport_type__in=['car', 'truck']).order_by('name', 'transport_type', 'pk').values('id', 'name', 'transport_type')),
            'models': list(CarModel.objects.filter(transport_type__in=['car', 'truck']).order_by('name', 'pk').values('id', 'name', 'brand_id')),
            'categories': service_options(seller.seller_type),
            'all_label': 'Все виды работ' if sto else 'Все услуги',
            'empty_label': 'Работы не выбраны' if sto else 'Услуги не выбраны',
            'error_label': 'Выберите хотя бы одну работу или услугу.',
        },
    }, status=400 if error else 200))
