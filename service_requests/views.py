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

match_services(req)

return JsonResponse({"success": True, "request_id": req.id})


def match_services(req):
    sellers = ServiceSeller.objects.filter(
        seller_type=req.service_type,
        city=req.city,
        is_active=True
    )

    req_services = set(req.services.values_list("name", flat=True))

    for seller in sellers:
        seller_services = set(seller.services.values_list("name", flat=True))

        if seller_services & req_services:
            ServiceMatch.objects.create(
                request=req,
                seller=seller
            )


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
        items.append({
            "id": req.id,
            "service_type": req.service_type,
            "services": list(req.services.values_list("name", flat=True)),
            "city": req.city,
            "phone": req.phone,
            "description": req.description,
            "status": match.status,
        })

    return JsonResponse({"requests": items})