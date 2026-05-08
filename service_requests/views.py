from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
import json

from .models import Service, ServiceSeller, ServiceRequest, ServiceMatch


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


def match_services(req):
    req_services = set(
        req.services.values_list("name", flat=True)
    )

    matched_sellers = []

    district_sellers = ServiceSeller.objects.filter(
        seller_type=req.service_type,
        city=req.city,
        district=req.district,
        is_active=True
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
            matched_sellers.append(seller)

    if not matched_sellers:
        city_sellers = ServiceSeller.objects.filter(
            seller_type=req.service_type,
            city=req.city,
            is_active=True
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