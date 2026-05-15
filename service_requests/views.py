from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.db.models import Q
from django.core.paginator import Paginator
import json

from .models import (
    Service,
    ServiceSeller,
    ServiceRequest,
    ServiceMatch,
    ServiceWhatsAppMessageLog,
    ServiceBroadcastSettings,
)

from core.views import (
    _normalize_whatsapp,
    _wa_template_param,
)


def read_json(request):
    try:
        return json.loads(request.body or "{}")
    except Exception:
        return {}


@csrf_exempt
def create_service_seller(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = read_json(request)

    seller = ServiceSeller.objects.create(
        name=data.get("name", "").strip(),
        whatsapp=data.get("whatsapp", "").strip(),
        password=data.get("password", ""),
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


@csrf_exempt
def service_seller_login(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = read_json(request)

    try:
        seller = ServiceSeller.objects.get(
            whatsapp=data.get("whatsapp", "").strip(),
            password=data.get("password", "")
        )
        return JsonResponse({"success": True, "seller_id": seller.id})
    except ServiceSeller.DoesNotExist:
        return JsonResponse({"error": "Неверный WhatsApp или пароль"}, status=400)


@csrf_exempt
def create_service_request(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = read_json(request)

    req = ServiceRequest.objects.create(
        service_type=data.get("service_type", "sto"),
        brand=data.get("brand", "").strip(),
        model=data.get("model", "").strip(),
        city=data.get("city", "").strip(),
        district=data.get("district", "").strip(),
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

    return JsonResponse({
        "success": True,
        "request_id": req.id,
        "sellers": sellers
    })


def send_service_whatsapp_to_seller(req, seller):

    import os
    import urllib.request
    import urllib.error

    phone_number_id = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
    access_token = os.getenv('WHATSAPP_ACCESS_TOKEN')
    template_name = os.getenv(
        'WHATSAPP_TEMPLATE_NAME',
        'zpt_request_notification'
    )
    template_lang = os.getenv(
        'WHATSAPP_TEMPLATE_LANG',
        'ru'
    )

    to_phone = _normalize_whatsapp(seller.whatsapp)

    services_text = ', '.join(
        req.services.values_list('name', flat=True)
    ) or '-'

    payload = {
        'messaging_product': 'whatsapp',
        'to': to_phone,
        'type': 'template',
        'template': {
            'name': template_name,
            'language': {
                'code': template_lang,
            },
            'components': [
                {
                    'type': 'body',
                    'parameters': [
                        _wa_template_param(req.id),
                        _wa_template_param(req.brand),
                        _wa_template_param(req.model),
                        _wa_template_param(services_text),
                        _wa_template_param(req.city),
                        _wa_template_param(req.description),
                        _wa_template_param(req.phone),
                    ],
                }
            ],
        },
    }

    url = f'https://graph.facebook.com/v20.0/{phone_number_id}/messages'

    body = json.dumps(
        payload,
        ensure_ascii=False
    ).encode('utf-8')

    http_request = urllib.request.Request(
        url,
        data=body,
        method='POST',
        headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
        },
    )

    try:

        with urllib.request.urlopen(
            http_request,
            timeout=20
        ) as response:

            response_body = response.read().decode('utf-8')

            response_json = json.loads(response_body)

            messages = response_json.get('messages') or []

            message_id = (
                messages[0].get('id', '')
                if messages else ''
            )

            ok = 200 <= response.status < 300

            ServiceWhatsAppMessageLog.objects.create(
                seller=seller,
                request=req,
                phone=to_phone,
                message_type='seller_request',
                status='sent' if ok else 'failed',
                meta_message_id=message_id,
                error_text='' if ok else response_body,
                response_json=response_body,
            )

            return {
                'ok': ok,
                'message_id': message_id,
                'response': response_json,
            }

    except urllib.error.HTTPError as e:

        error_body = e.read().decode('utf-8')

        ServiceWhatsAppMessageLog.objects.create(
            seller=seller,
            request=req,
            phone=to_phone,
            message_type='seller_request',
            status='failed',
            error_text=error_body,
            response_json=error_body,
        )

        return {
            'ok': False,
            'error': error_body,
        }

    except Exception as e:

        ServiceWhatsAppMessageLog.objects.create(
            seller=seller,
            request=req,
            phone=to_phone,
            message_type='seller_request',
            status='failed',
            error_text=str(e),
        )

        return {
            'ok': False,
            'error': str(e),
        }




def match_services(req):

    req_services = set(
        req.services.values_list("name", flat=True)
    )

    matched_sellers = []

    settings = ServiceBroadcastSettings.objects.first()

    district_sellers = ServiceSeller.objects.filter(
        seller_type=req.service_type,
        city=req.city,
        district=req.district,
        is_active=True
    )

    if settings and settings.mode == ServiceBroadcastSettings.MODE_TEST:
        district_sellers = district_sellers.filter(
            is_test_seller=True
        )

    for seller in district_sellers:

        seller_services = set(
            seller.services.values_list("name", flat=True)
        )

        if seller_services & req_services:

            ServiceMatch.objects.create(
                request=req,
                seller=seller
            )

            send_service_whatsapp_to_seller(req, seller)

            matched_sellers.append(seller)

    if not matched_sellers:

        city_sellers = ServiceSeller.objects.filter(
            seller_type=req.service_type,
            city=req.city,
            is_active=True
        )

        if settings and settings.mode == ServiceBroadcastSettings.MODE_TEST:
            city_sellers = city_sellers.filter(
                is_test_seller=True
            )

        for seller in city_sellers:

            seller_services = set(
                seller.services.values_list("name", flat=True)
            )

            if seller_services & req_services:

                ServiceMatch.objects.create(
                    request=req,
                    seller=seller
                )

                send_service_whatsapp_to_seller(req, seller)

                matched_sellers.append(seller)

    return matched_sellers

def get_service_requests(request):
    seller_id = request.GET.get("seller_id")

    if not seller_id:
        return JsonResponse({"requests": []})

    matches = ServiceMatch.objects.filter(
        seller_id=seller_id
    ).select_related("request").prefetch_related("request__services").order_by("-created_at")

    items = []

    for match in matches:
        req = match.request

        if match.status == 'new':
            match.status = 'viewed'
            match.save(update_fields=['status'])

        items.append({
            "id": req.id,
            "service_type": req.service_type,
            "services": list(req.services.values_list("name", flat=True)),
            "city": req.city,
            "district": req.district,
            "phone": req.phone,
            "description": req.description,
            "status": match.status,
        })

    return JsonResponse({"requests": items})


@csrf_exempt
def get_service_seller_profile(request):
    seller_id = request.GET.get("seller_id")

    if not seller_id:
        return JsonResponse({"error": "seller_id required"}, status=400)

    try:
        seller = ServiceSeller.objects.get(id=seller_id)

        return JsonResponse({
            "id": seller.id,
            "name": seller.name,
            "whatsapp": seller.whatsapp,
            "city": seller.city,
            "district": seller.district,
            "address": seller.address,
            "map_link": seller.map_link,
            "seller_type": seller.seller_type,
            "services": list(
                seller.services.values_list("name", flat=True)
            ),
            "is_active": seller.is_active,
        })

    except ServiceSeller.DoesNotExist:
        return JsonResponse({"error": "Исполнитель не найден"}, status=404)


@csrf_exempt
def update_service_seller_profile(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = read_json(request)

    seller_id = data.get("seller_id")

    if not seller_id:
        return JsonResponse({"error": "seller_id required"}, status=400)

    try:
        seller = ServiceSeller.objects.get(id=seller_id)

        seller.name = data.get("name", seller.name).strip()
        seller.city = data.get("city", seller.city).strip()
        seller.district = data.get("district", seller.district).strip()
        seller.address = data.get("address", seller.address).strip()
        seller.map_link = data.get("map_link", seller.map_link).strip()
        seller.seller_type = data.get("seller_type", seller.seller_type)
        seller.is_active = data.get("is_active", seller.is_active)

        new_password = data.get("password", "").strip()

        if new_password:
            seller.password = new_password

        seller.services.clear()

        for name in data.get("services", []):
            service, _ = Service.objects.get_or_create(name=name)
            seller.services.add(service)

        seller.save()

        return JsonResponse({"success": True})

    except ServiceSeller.DoesNotExist:
        return JsonResponse({"error": "Исполнитель не найден"}, status=404)


@csrf_exempt
def update_service_match_status(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = read_json(request)

    seller_id = data.get("seller_id")
    request_id = data.get("request_id")
    status = data.get("status")

    allowed = ['new', 'sent', 'viewed', 'in_work', 'done']

    if status not in allowed:
        return JsonResponse({"error": "Invalid status"}, status=400)

    try:
        match = ServiceMatch.objects.get(
            seller_id=seller_id,
            request_id=request_id
        )

        match.status = status
        match.save()

        return JsonResponse({"success": True})

    except ServiceMatch.DoesNotExist:
        return JsonResponse({"error": "Match not found"}, status=404)


def service_request_result(request, request_id):

    req = get_object_or_404(
        ServiceRequest,
        id=request_id
    )

    matches = ServiceMatch.objects.filter(
        request=req
    ).select_related('seller')

    sellers = []

    for match in matches:

        seller = match.seller

        sellers.append({
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

    return render(
        request,
        'service-request/result.html',
        {
            'req': req,
            'sellers': sellers,
            'service_type_label': service_type_label,
            'sellers_count': len(sellers),
        }
    )


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