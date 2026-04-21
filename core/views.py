import json

from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import Request, Country, Brand, CarModel, Seller, Match


def countries_list(request):
    countries = Country.objects.all().order_by('name')
    data = [
        {
            'id': country.id,
            'name': country.name,
        }
        for country in countries
    ]
    return JsonResponse(data, safe=False)


def brands_by_country(request):
    country_id = request.GET.get('country_id')
    transport_type = request.GET.get('transport_type')

    brands = Brand.objects.all().order_by('name')

    if country_id:
        brands = brands.filter(country_id=country_id)

    if transport_type:
        brands = brands.filter(transport_type=transport_type)

    data = [
        {
            'id': brand.id,
            'name': brand.name,
            'country_id': brand.country_id,
            'transport_type': brand.transport_type,
        }
        for brand in brands
    ]
    return JsonResponse(data, safe=False)


def models_by_brand(request):
    brand_id = request.GET.get('brand_id')
    transport_type = request.GET.get('transport_type')

    models = CarModel.objects.select_related('brand').all().order_by('name')

    if brand_id:
        models = models.filter(brand_id=brand_id)

    if transport_type:
        models = models.filter(transport_type=transport_type)

    data = [
        {
            'id': model.id,
            'name': model.name,
            'brand_id': model.brand_id,
            'transport_type': model.transport_type,
        }
        for model in models
    ]
    return JsonResponse(data, safe=False)


def _base_sellers_queryset(req):
    sellers = Seller.objects.filter(
        is_active=True,
        is_paused=False,
        transport_type=req.transport_type
    )

    if req.category:
        sellers = sellers.filter(category=req.category)

    return sellers.distinct()


def _apply_city_filter(qs, req):
    if req.city:
        qs = qs.filter(city=req.city)
    return qs


def _apply_country_filter(qs, req):
    if req.country:
        qs = qs.filter(
            Q(country_fk__name=req.country) | Q(country_fk__isnull=True)
        )
    return qs


def _apply_brand_filter(qs, req):
    if req.brand:
        qs = qs.filter(
            Q(brand=req.brand) | Q(brand_fk__name=req.brand)
        )
    return qs


def _apply_model_filter(qs, req):
    if req.model:
        qs = qs.filter(
            Q(model=req.model) | Q(model_fk__name=req.model)
        )
    return qs


def _find_matching_sellers(req):
    base_qs = _base_sellers_queryset(req)

    strategies = [
        {
            'name': 'exact',
            'city': True,
            'country': True,
            'brand': True,
            'model': True,
        },
        {
            'name': 'without_model',
            'city': True,
            'country': True,
            'brand': True,
            'model': False,
        },
        {
            'name': 'without_brand_model',
            'city': True,
            'country': True,
            'brand': False,
            'model': False,
        },
        {
            'name': 'without_country_brand_model',
            'city': True,
            'country': False,
            'brand': False,
            'model': False,
        },
        {
            'name': 'category_only',
            'city': False,
            'country': False,
            'brand': False,
            'model': False,
        },
    ]

    for strategy in strategies:
        qs = base_qs

        if strategy['city']:
            qs = _apply_city_filter(qs, req)

        if strategy['country']:
            qs = _apply_country_filter(qs, req)

        if strategy['brand']:
            qs = _apply_brand_filter(qs, req)

        if strategy['model']:
            qs = _apply_model_filter(qs, req)

        qs = qs.distinct()

        if qs.exists():
            return qs, strategy['name']

    return Seller.objects.none(), 'no_match'


@csrf_exempt
def create_request(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'invalid method'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'invalid json'}, status=400)

    req = Request.objects.create(
        transport_type=data.get('transport_type'),
        country=data.get('country', ''),
        brand=data.get('brand', ''),
        model=data.get('model', ''),
        category=data.get('category', ''),
        article=data.get('article', ''),
        description=data.get('description', ''),
        city=data.get('city', ''),
        phone=data.get('phone', ''),
    )

    sellers, strategy_used = _find_matching_sellers(req)

    matches_created = 0

    for seller in sellers:
        _, created = Match.objects.get_or_create(
            request=req,
            seller=seller,
            defaults={'status': 'prepared'}
        )
        if created:
            matches_created += 1

    return JsonResponse({
        'status': 'ok',
        'id': req.id,
        'matches': matches_created,
        'strategy': strategy_used,
    })


@csrf_exempt
def create_seller(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'invalid method'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'invalid json'}, status=400)

    name = (data.get('name') or '').strip()
    whatsapp = (data.get('whatsapp') or '').strip()
    transport_type = (data.get('transport_type') or '').strip()
    city = (data.get('city') or '').strip()
    category = (data.get('category') or '').strip()

    country_id = data.get('country_id')
    brand_id = data.get('brand_id')
    model_id = data.get('model_id')

    if not name:
        return JsonResponse({'error': 'Укажите имя или название продавца'}, status=400)

    if not whatsapp:
        return JsonResponse({'error': 'Укажите WhatsApp'}, status=400)

    if transport_type not in ['car', 'truck']:
        return JsonResponse({'error': 'Некорректный тип транспорта'}, status=400)

    if not city:
        return JsonResponse({'error': 'Укажите город'}, status=400)

    if not category:
        return JsonResponse({'error': 'Выберите категорию'}, status=400)

    country = None
    brand = None
    car_model = None

    if country_id:
        country = Country.objects.filter(id=country_id).first()
        if not country:
            return JsonResponse({'error': 'Страна не найдена'}, status=400)

    if brand_id:
        brand = Brand.objects.filter(id=brand_id, transport_type=transport_type).first()
        if not brand:
            return JsonResponse({'error': 'Марка не найдена'}, status=400)

    if model_id:
        car_model = CarModel.objects.filter(id=model_id, transport_type=transport_type).first()
        if not car_model:
            return JsonResponse({'error': 'Модель не найдена'}, status=400)

    if brand and country and brand.country_id != country.id:
        return JsonResponse({'error': 'Марка не относится к выбранной стране'}, status=400)

    if car_model and brand and car_model.brand_id != brand.id:
        return JsonResponse({'error': 'Модель не относится к выбранной марке'}, status=400)

    seller = Seller.objects.create(
        name=name,
        whatsapp=whatsapp,
        transport_type=transport_type,
        city=city,
        category=category,
        brand=brand.name if brand else '',
        model=car_model.name if car_model else '',
        country_fk=country,
        brand_fk=brand,
        model_fk=car_model,
        is_active=True,
        is_paused=False,
    )

    return JsonResponse({
        'status': 'ok',
        'id': seller.id,
        'message': 'Продавец зарегистрирован'
    })