# --- ВСТАВЬ ПОЛНОСТЬЮ ---

import json
from datetime import timedelta
from urllib.parse import quote

from django.contrib.auth.hashers import make_password, check_password
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import (
    Request,
    Country,
    Brand,
    CarModel,
    PartCategory,
    Seller,
    Match,
    RequestDispatch,
    BroadcastSettings,
)


WAVE_SIZE = 10
WAVE_INTERVAL_MINUTES = 5
TEMP_SELLER_PASSWORD = 'zpt2026'


def _normalize_whatsapp(phone):
    return ''.join(ch for ch in str(phone or '') if ch.isdigit())


def countries_list(request):
    countries = Country.objects.all().order_by('name')
    return JsonResponse([{'id': c.id, 'name': c.name} for c in countries], safe=False)


def brands_by_country(request):
    country_id = request.GET.get('country_id')
    transport_type = request.GET.get('transport_type')

    brands = Brand.objects.all().order_by('name')

    if country_id:
        brands = brands.filter(country_id=country_id)

    if transport_type:
        brands = brands.filter(transport_type=transport_type)

    return JsonResponse([
        {'id': b.id, 'name': b.name}
        for b in brands
    ], safe=False)


def models_by_brand(request):
    brand_id = request.GET.get('brand_id')
    transport_type = request.GET.get('transport_type')

    models = CarModel.objects.all().order_by('name')

    if brand_id:
        models = models.filter(brand_id=brand_id)

    if transport_type:
        models = models.filter(transport_type=transport_type)

    return JsonResponse([
        {'id': m.id, 'name': m.name}
        for m in models
    ], safe=False)


def part_categories_list(request):
    categories = PartCategory.objects.all().order_by('name')
    return JsonResponse(
        [{'id': c.id, 'name': c.name} for c in categories],
        safe=False
    )


# ===================== SELLER =====================

@csrf_exempt
def create_seller(request):
    data = json.loads(request.body)

    seller = Seller.objects.create(
        name=data['name'],
        whatsapp=_normalize_whatsapp(data['whatsapp']),
        password_hash=make_password(data['password']),
        transport_type=data['transport_type'],
        city=data.get('city', ''),
    )

    return JsonResponse({'status': 'ok', 'id': seller.id})


@csrf_exempt
def seller_login(request):
    data = json.loads(request.body)

    try:
        seller = Seller.objects.get(
            whatsapp=_normalize_whatsapp(data['whatsapp'])
        )
    except Seller.DoesNotExist:
        return JsonResponse({'error': 'Неверный WhatsApp'}, status=400)

    if not check_password(data['password'], seller.password_hash):
        return JsonResponse({'error': 'Неверный пароль'}, status=400)

    request.session['seller_id'] = seller.id

    return JsonResponse({
        'status': 'ok',
        'seller_id': seller.id,
        'must_change_password': seller.must_change_password,
    })


@csrf_exempt
def seller_logout(request):
    request.session.flush()
    return JsonResponse({'status': 'ok'})


def _get_logged_seller(request):
    seller_id = request.session.get('seller_id')
    if not seller_id:
        return None
    return Seller.objects.filter(id=seller_id).first()


def seller_profile(request):
    seller = _get_logged_seller(request)
    if not seller:
        return JsonResponse({'error': 'auth required'}, status=401)

    return JsonResponse({
        'id': seller.id,
        'name': seller.name,
        'whatsapp': seller.whatsapp,
        'phone2': seller.phone2,
        'city': seller.city,
        'market_location': seller.market_location,

        # 🔥 ВАЖНО
        'receive_requests': seller.receive_requests,
        'is_test_seller': seller.is_test_seller,

        'selected_categories': list(seller.selected_categories.values('id', 'name')),
        'selected_countries': list(seller.selected_countries.values('id', 'name')),
        'selected_brands': list(seller.selected_brands.values('id', 'name')),
        'selected_models': list(seller.selected_models.values('id', 'name')),

        'all_categories': seller.all_categories,
        'all_countries': seller.all_countries,
        'all_brands': seller.all_brands,
        'all_models': seller.all_models,
    })


@csrf_exempt
def update_seller_profile(request):
    seller = _get_logged_seller(request)
    if not seller:
        return JsonResponse({'error': 'auth required'}, status=401)

    data = json.loads(request.body)

    # базовые поля
    seller.phone2 = data.get('phone2', '')
    seller.city = data.get('city', '')
    seller.market_location = data.get('market_location', '')

    # 🔥 флаги
    seller.receive_requests = data.get('receive_requests', False)
    seller.is_test_seller = data.get('is_test_seller', False)

    seller.all_categories = data.get('all_categories', False)
    seller.all_countries = data.get('all_countries', False)
    seller.all_brands = data.get('all_brands', False)
    seller.all_models = data.get('all_models', False)

    seller.save()

    # many-to-many
    if not seller.all_categories:
        seller.selected_categories.set(data.get('selected_category_ids', []))
    else:
        seller.selected_categories.clear()

    if not seller.all_countries:
        seller.selected_countries.set(data.get('selected_country_ids', []))
    else:
        seller.selected_countries.clear()

    if not seller.all_brands:
        seller.selected_brands.set(data.get('selected_brand_ids', []))
    else:
        seller.selected_brands.clear()

    if not seller.all_models:
        seller.selected_models.set(data.get('selected_model_ids', []))
    else:
        seller.selected_models.clear()

    return JsonResponse({'status': 'ok'})


# ===================== REQUEST =====================

@csrf_exempt
def create_request(request):
    data = json.loads(request.body)

    req = Request.objects.create(
        transport_type=data.get('transport_type'),
        country=data.get('country'),
        brand=data.get('brand'),
        model=data.get('model'),
        category=data.get('category'),
        description=data.get('description'),
        phone=data.get('phone'),
        city=data.get('city'),
    )

    sellers = Seller.objects.filter(
        receive_requests=True,
        is_active=True
    )

    result = []

    for s in sellers:
        link = f"https://wa.me/{s.whatsapp}?text=Заявка%20#{req.id}"
        result.append({'seller': s.name, 'wa_link': link})

    return JsonResponse({
        'status': 'ok',
        'id': req.id,
        'seller_notifications': result
    })