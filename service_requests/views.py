from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.db.models import Q
from django.core.paginator import Paginator
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
import json

from .location import normalize_service_request_location
from .services.dispatch_policy import select_service_sellers_for_request
from .services.whatsapp_dispatch import enqueue_service_request_dispatches
from .services.service_seller_identity import (
    ServiceSellerAccessDenied,
    authenticate_service_seller,
    get_current_service_seller,
)
from .models import (
    Service,
    ServiceSeller,
    ServiceRequest,
    ServiceMatch,
)

from core.phone_utils import build_whatsapp_url


def _service_request_whatsapp_url(req):
    services = ', '.join(req.services.values_list('name', flat=True))
    text = (
        f'Здравствуйте. По заявке ZPT:\n'
        f'Услуги: {services}\n'
        f'Город: {req.city or ""}\n'
        f'Описание: {req.description or ""}'
    )
    return build_whatsapp_url(req.phone, text)


def read_json(request):
    try:
        return json.loads(request.body or "{}")
    except Exception:
        return {}


def _password_validation_error(password):
    if not password:
        return 'Укажите пароль'

    try:
        validate_password(password)
    except ValidationError as exc:
        return '; '.join(exc.messages)

    return None


def build_service_request_success_payload(
    req: ServiceRequest,
    sellers: list[dict],
) -> dict:
    sellers_count = len(sellers)
    services_names = list(req.services.values_list('name', flat=True))

    if sellers_count > 0:
        title = '✅ Заявка принята.'
        message = (
            'Подходящие исполнители найдены. '
            'Мы уведомим их в WhatsApp.'
        )
        timing_hint = 'Обычно первые ответы приходят в течение 5–15 минут.'
        catalog_hint = (
            'Если хотите самостоятельно посмотреть исполнителей — '
            'можете открыть каталог.'
        )
        result_button_label = 'Посмотреть исполнителей по заявке'
    else:
        title = '✅ Заявка принята.'
        message = (
            'В выбранном городе пока нет зарегистрированных исполнителей. '
            'Мы сохранили вашу заявку.'
        )
        timing_hint = ''
        catalog_hint = 'Вы можете посмотреть каталог исполнителей в других городах.'
        result_button_label = 'Посмотреть страницу заявки'

    return {
        'success': True,
        'request_id': req.id,
        'title': title,
        'message': message,
        'timing_hint': timing_hint,
        'catalog_hint': catalog_hint,
        'result_button_label': result_button_label,
        'sellers_count': sellers_count,
        'service_type': req.service_type,
        'services': services_names,
        'city': req.city,
        'district': req.district,
        'phone': req.phone,
        'description': req.description,
        'result_url': reverse(
            'service_request_result_page',
            kwargs={
                'request_id': req.id,
                'access_token': req.access_token,
            },
        ),
        'sellers': sellers,
    }


@csrf_exempt
def create_service_seller(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = read_json(request)

    password = (data.get("password") or "").strip()
    password_error = _password_validation_error(password)

    if password_error:
        return JsonResponse({"error": password_error}, status=400)

    whatsapp = data.get("whatsapp", "").strip()

    if ServiceSeller.objects.filter(whatsapp=whatsapp).exists():
        return JsonResponse(
            {"error": "Исполнитель с таким WhatsApp уже зарегистрирован"},
            status=400,
        )

    seller = ServiceSeller.objects.create(
        name=data.get("name", "").strip(),
        whatsapp=whatsapp,
        password=make_password(password),
        city=data.get("city", "").strip(),
        district=data.get("district", "").strip(),
        address=data.get("address", "").strip(),
        map_link=data.get("map_link", "").strip(),
        seller_type=data.get("seller_type", "sto"),
    )

    for name in data.get("services", []):
        service, _ = Service.objects.get_or_create(name=name)
        seller.services.add(service)

    return JsonResponse({"success": True, "seller_id": seller.id})


def _service_seller_or_error(request):
    try:
        return get_current_service_seller(request), None
    except ServiceSellerAccessDenied as exc:
        return None, JsonResponse({'error': exc.message}, status=exc.status)


def service_seller_login(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = read_json(request)

    seller = authenticate_service_seller(
        request,
        data.get("whatsapp", ""),
        data.get("password", ""),
    )

    if seller:
        return JsonResponse({"success": True, "seller_id": seller.id})

    return JsonResponse({"error": "Неверный WhatsApp или пароль"}, status=400)


@csrf_exempt
def create_service_request(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = read_json(request)

    try:
        city, district = normalize_service_request_location(
            data.get('city', ''),
            data.get('district', ''),
        )
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    with transaction.atomic():
        req = ServiceRequest.objects.create(
            service_type=data.get("service_type", "sto"),
            brand=data.get("brand", "").strip(),
            model=data.get("model", "").strip(),
            city=city,
            district=district,
            phone=data.get("phone", "").strip(),
            description=data.get("description", "").strip(),
        )

        for name in data.get("services", []):
            service, _ = Service.objects.get_or_create(name=name)
            req.services.add(service)

        matched = match_services(req)

    sellers = []

    for seller in matched:
        sellers.append({
            "name": seller.name,
            "whatsapp": seller.whatsapp,
            "district": seller.district,
            "address": seller.address,
            "map_link": seller.map_link,
        })

    return JsonResponse(
        build_service_request_success_payload(req, sellers),
    )


def match_services(req):
    return enqueue_service_request_dispatches(
        req,
        select_service_sellers_for_request(req),
    )

def _service_request_item(match):
    req = match.request
    return {
        "id": req.id,
        "service_type": req.service_type,
        "services": list(req.services.values_list("name", flat=True)),
        "city": req.city,
        "district": req.district,
        "phone": req.phone,
        "whatsapp_url": _service_request_whatsapp_url(req),
        "description": req.description,
        "status": match.status,
    }


def _service_seller_profile_payload(seller):
    return {
        "id": seller.id,
        "name": seller.name,
        "whatsapp": seller.whatsapp,
        "city": seller.city,
        "district": seller.district,
        "address": seller.address,
        "map_link": seller.map_link,
        "instagram": seller.instagram,
        "website": seller.website,
        "working_hours": seller.working_hours,
        "description": seller.description,
        "seller_type": seller.seller_type,
        "services": list(seller.services.values_list("name", flat=True)),
        "is_active": seller.is_active,
    }


def get_service_requests(request):
    seller, error = _service_seller_or_error(request)
    if error:
        return error

    matches = ServiceMatch.objects.filter(
        seller=seller
    ).select_related("request").prefetch_related("request__services").order_by("-created_at")

    return JsonResponse({
        "requests": [_service_request_item(match) for match in matches],
    })


def get_service_seller_profile(request):
    seller, error = _service_seller_or_error(request)
    if error:
        return error
    return JsonResponse(_service_seller_profile_payload(seller))


def update_service_seller_profile(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    seller, error = _service_seller_or_error(request)
    if error:
        return error

    data = read_json(request)
    new_password = (data.get("password") or "").strip()
    if new_password:
        password_error = _password_validation_error(new_password)
        if password_error:
            return JsonResponse({"error": password_error}, status=400)

    service_names = data.get("services", [])
    fields = {
        "name": data.get("name", seller.name).strip(),
        "city": data.get("city", seller.city).strip(),
        "district": data.get("district", seller.district).strip(),
        "address": data.get("address", seller.address).strip(),
        "map_link": data.get("map_link", seller.map_link).strip(),
        "instagram": data.get("instagram", seller.instagram or "").strip(),
        "website": data.get("website", seller.website or "").strip(),
        "working_hours": data.get("working_hours", seller.working_hours or "").strip(),
        "description": data.get("description", seller.description or "").strip(),
        "seller_type": data.get("seller_type", seller.seller_type),
        "is_active": data.get("is_active", seller.is_active),
    }

    with transaction.atomic():
        seller = ServiceSeller.objects.select_for_update().get(pk=seller.pk)
        for field, value in fields.items():
            setattr(seller, field, value)
        if new_password:
            seller.password = ''
        seller.save()
        seller.services.clear()
        for name in service_names:
            service, _ = Service.objects.get_or_create(name=name)
            seller.services.add(service)
        if new_password:
            request.user.set_password(new_password)
            request.user.save(update_fields=['password'])

    if new_password:
        update_session_auth_hash(request, request.user)

    return JsonResponse({"success": True})


def update_service_match_status(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    seller, error = _service_seller_or_error(request)
    if error:
        return error

    data = read_json(request)
    status = data.get("status")
    allowed = ['new', 'sent', 'viewed', 'in_work', 'done']
    if status not in allowed:
        return JsonResponse({"error": "Invalid status"}, status=400)

    try:
        match = ServiceMatch.objects.get(
            seller=seller,
            request_id=data.get("request_id"),
        )
    except (ServiceMatch.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"error": "Match not found"}, status=404)

    match.status = status
    match.save(update_fields=['status'])
    return JsonResponse({"success": True})


def mark_service_requests_viewed(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    seller, error = _service_seller_or_error(request)
    if error:
        return error

    ServiceMatch.objects.filter(seller=seller, status='new').update(status='viewed')
    return JsonResponse({"success": True})


def service_seller_logout(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    logout(request)
    return JsonResponse({"success": True})


def _service_result_privacy(response):
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'no-referrer'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


def service_request_legacy_result(request, request_id):
    response = render(
        request,
        'service-request/result_unavailable.html',
        status=404,
    )
    return _service_result_privacy(response)


def service_request_result(request, request_id, access_token):

    req = get_object_or_404(
        ServiceRequest,
        id=request_id,
        access_token=access_token,
    )

    matches = ServiceMatch.objects.filter(
        request=req
    ).select_related('seller')

    sellers = []

    for match in matches:

        seller = match.seller

        sellers.append({
            'id': seller.id,
            'name': seller.name,
            'district': seller.district,
            'address': seller.address,
            'map_link': seller.map_link,
            'whatsapp': seller.whatsapp,
        })

    service_type_label = {
        'sto': 'СТО / ремонт',
        'detailing': 'Детейлинг / тюнинг',
    }.get(req.service_type, req.service_type)

    response = render(
        request,
        'service-request/result.html',
        {
            'req': req,
            'sellers': sellers,
            'service_type_label': service_type_label,
            'sellers_count': len(sellers),
        }
    )
    return _service_result_privacy(response)


def services_catalog(request):

    q = request.GET.get('q', '').strip()
    seller_type = request.GET.get('type', '').strip()
    city = request.GET.get('city', '').strip()
    district = request.GET.get('district', '').strip()
    service_name = request.GET.get('service', '').strip()
    page = request.GET.get('page', 1)

    sto_services = [
        'Диагностика',
        'Ходовая часть',
        'Двигатель',
        'Тормозная система',
        'Электрика',
        'Кузовной ремонт',
        'Ремонт АКПП',
        'Шиномонтаж',
        'Развал-схождение',
        'Автоэлектрик',
    ]

    detailing_services = [
        'Мойка',
        'Химчистка',
        'Полировка',
        'Керамика',
        'Антигравийная плёнка',
        'Тонировка',
        'Шумоизоляция',
        'Перетяжка салона',
        'Автозвук',
        'Свет / оптика',
        'Внешний тюнинг',
    ]

    sellers = ServiceSeller.objects.filter(
        is_active=True,
    ).prefetch_related('services')

    if seller_type:
        sellers = sellers.filter(
            seller_type=seller_type
        )

    if city:
        sellers = sellers.filter(
            city=city
        )

    if district:
        sellers = sellers.filter(
            district=district
        )

    if service_name:
        sellers = sellers.filter(
            services__name=service_name
        )

    if q:
        sellers = sellers.filter(
            Q(name__icontains=q) |
            Q(address__icontains=q) |
            Q(district__icontains=q) |
            Q(services__name__icontains=q)
        ).distinct()

    sellers_data = []

    for seller in sellers:

        services_text = ', '.join(
            seller.services.values_list(
                'name',
                flat=True
            )
        )

        filled_fields = 0
        total_fields = 7

        if seller.address:
            filled_fields += 1

        if seller.district:
            filled_fields += 1

        if seller.map_link:
            filled_fields += 1

        if seller.city:
            filled_fields += 1

        if seller.name:
            filled_fields += 1

        if services_text:
            filled_fields += 1

        if seller.whatsapp:
            filled_fields += 1

        percent = int(
            (filled_fields / total_fields) * 100
        )

        stars_count = round(percent / 20)

        stars = (
            '★' * stars_count
            + '☆' * (5 - stars_count)
        )

        seller_type_label = {
            'sto': 'СТО / ремонт',
            'detailing': 'Детейлинг / тюнинг',
        }.get(
            seller.seller_type,
            seller.seller_type
        )

        sellers_data.append({
            'id': seller.id,
            'name': seller.name,
            'city': seller.city,
            'district': seller.district,
            'address': seller.address,
            'map_link': seller.map_link,
            'whatsapp': seller.whatsapp,
            'services': services_text,
            'seller_type_label': seller_type_label,
            'profile_percent': percent,
            'profile_stars': stars,
        })

    paginator = Paginator(
        sellers_data,
        20
    )

    sellers_page = paginator.get_page(
        page
    )

    return render(
        request,
        'catalog/services/index.html',
        {
            'sellers': sellers_page,
            'filters': {
                'q': q,
                'type': seller_type,
                'city': city,
                'district': district,
                'service': service_name,
            },
            'sto_services': sto_services,
            'detailing_services': detailing_services,
            'page_obj': sellers_page,
        }
    )


def service_seller_detail(request, seller_id):

    seller = get_object_or_404(
        ServiceSeller,
        id=seller_id,
        is_active=True
    )

    services_text = ', '.join(
        seller.services.values_list(
            'name',
            flat=True
        )
    )

    filled_fields = 0
    total_fields = 7

    if seller.address:
        filled_fields += 1

    if seller.district:
        filled_fields += 1

    if seller.map_link:
        filled_fields += 1

    if seller.city:
        filled_fields += 1

    if seller.name:
        filled_fields += 1

    if services_text:
        filled_fields += 1

    if seller.whatsapp:
        filled_fields += 1

    percent = int(
        (filled_fields / total_fields) * 100
    )

    stars_count = round(percent / 20)

    stars = (
        '★' * stars_count
        + '☆' * (5 - stars_count)
    )

    seller_type_label = {
        'sto': 'СТО / ремонт',
        'detailing': 'Детейлинг / тюнинг',
    }.get(
        seller.seller_type,
        seller.seller_type
    )

    return render(
        request,
        'catalog/services/detail.html',
        {
            'seller': seller,
            'services_text': services_text,
            'profile_percent': percent,
            'profile_stars': stars,
            'seller_type_label': seller_type_label,
        }
    )