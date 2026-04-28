import json
from datetime import timedelta
from urllib.parse import quote

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
)


WAVE_SIZE = 10
WAVE_INTERVAL_MINUTES = 10


def countries_list(request):
    countries = Country.objects.all().order_by('name')
    data = [{'id': country.id, 'name': country.name} for country in countries]
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


def part_categories_list(request):
    categories = PartCategory.objects.all().order_by('name')
    data = [{'id': category.id, 'name': category.name} for category in categories]
    return JsonResponse(data, safe=False)


def _base_sellers_queryset(req):
    return Seller.objects.filter(
        is_active=True,
        is_paused=False,
        transport_type=req.transport_type
    ).distinct()


def _apply_city_filter(qs, req):
    if req.city:
        qs = qs.filter(city=req.city)
    return qs.distinct()


def _apply_country_filter(qs, req):
    if not req.country:
        return qs

    return qs.filter(
        Q(all_countries=True) |
        Q(country_fk__name=req.country) |
        Q(selected_countries__name=req.country)
    ).distinct()


def _apply_brand_filter(qs, req):
    if not req.brand:
        return qs

    return qs.filter(
        Q(all_brands=True) |
        Q(brand=req.brand) |
        Q(brand_fk__name=req.brand) |
        Q(selected_brands__name=req.brand)
    ).distinct()


def _apply_model_filter(qs, req):
    if not req.model:
        return qs

    return qs.filter(
        Q(all_brands=True) |
        Q(all_models=True) |
        Q(model=req.model) |
        Q(model_fk__name=req.model) |
        Q(selected_models__name=req.model)
    ).distinct()


def _find_matching_sellers(req):
    base_qs = _base_sellers_queryset(req)

    if req.category:
        base_qs = base_qs.filter(
            Q(all_categories=True) |
            Q(category=req.category) |
            Q(selected_categories__name=req.category)
        ).distinct()

    strategies = [
        {'name': 'exact_multi', 'city': True, 'country': True, 'brand': True, 'model': True},
        {'name': 'brand_category', 'city': True, 'country': True, 'brand': True, 'model': False},
        {'name': 'category_city', 'city': True, 'country': False, 'brand': False, 'model': False},
        {'name': 'category_only', 'city': False, 'country': False, 'brand': False, 'model': False},
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

        qs = qs.distinct().order_by('dispatch_priority', 'id')

        if qs.exists():
            return qs, strategy['name']

    return Seller.objects.none(), 'no_match'


def _normalize_whatsapp(phone):
    return ''.join(ch for ch in str(phone or '') if ch.isdigit())


def _seller_notification_text(req):
    return (
        f"Новая заявка №{req.id} с ZPT.KZ\n\n"
        f"Марка: {req.brand}\n"
        f"Модель: {req.model}\n"
        f"Категория: {req.category}\n"
        f"Город: {req.city}\n\n"
        f"Комментарий:\n"
        f"{req.description or '-'}\n\n"
        f"Телефон клиента:\n"
        f"{req.phone}\n\n"
        f"Свяжитесь с клиентом и предложите цену и наличие.\n\n"
        f"По вопросам сервиса и поддержки:\n"
        f"WhatsApp +7 771 360 7040\n\n"
        f"https://zpt.kz"
    )


def _buyer_to_seller_text(req):
    return (
        f"Здравствуйте! Я оставил заявку №{req.id} на ZPT.KZ\n\n"
        f"Марка: {req.brand}\n"
        f"Модель: {req.model}\n"
        f"Категория: {req.category}\n"
        f"Город: {req.city}\n"
        f"Артикул: {req.article or '-'}\n\n"
        f"Описание:\n"
        f"{req.description or '-'}\n\n"
        f"Подскажите, есть ли в наличии и какая цена?"
    )


def _seller_notification_link(seller_whatsapp, req):
    phone = _normalize_whatsapp(seller_whatsapp)
    text = quote(_seller_notification_text(req))
    return f"https://wa.me/{phone}?text={text}"


def _buyer_contact_link(seller_whatsapp, req):
    phone = _normalize_whatsapp(seller_whatsapp)
    text = quote(_buyer_to_seller_text(req))
    return f"https://wa.me/{phone}?text={text}"


def _mark_dispatch_sent(dispatch):
    now = timezone.now()

    match, created = Match.objects.get_or_create(
        request=dispatch.request,
        seller=dispatch.seller,
        defaults={
            'status': 'prepared',
            'sent_at': now,
        }
    )

    if not created and not match.sent_at:
        match.sent_at = now
        match.save(update_fields=['sent_at'])

    if dispatch.status != RequestDispatch.STATUS_SENT:
        dispatch.status = RequestDispatch.STATUS_SENT
        dispatch.sent_at = now
        dispatch.save(update_fields=['status', 'sent_at'])


def _dispatch_due_requests():
    now = timezone.now()

    due_dispatches = RequestDispatch.objects.select_related(
        'request',
        'seller'
    ).filter(
        status=RequestDispatch.STATUS_QUEUED,
        scheduled_at__lte=now
    ).order_by('scheduled_at', 'position_number')

    processed_count = 0

    for dispatch in due_dispatches:
        _mark_dispatch_sent(dispatch)
        processed_count += 1

    return processed_count


def _build_dispatch_queue(req, sellers):
    now = timezone.now()
    dispatches = []

    for index, seller in enumerate(sellers, start=1):
        wave_number = ((index - 1) // WAVE_SIZE) + 1
        scheduled_at = now + timedelta(minutes=(wave_number - 1) * WAVE_INTERVAL_MINUTES)

        dispatch, created = RequestDispatch.objects.get_or_create(
            request=req,
            seller=seller,
            defaults={
                'wave_number': wave_number,
                'position_number': index,
                'status': RequestDispatch.STATUS_QUEUED,
                'scheduled_at': scheduled_at,
            }
        )

        dispatches.append(dispatch)

    for dispatch in dispatches:
        if dispatch.wave_number == 1:
            _mark_dispatch_sent(dispatch)

    return dispatches


def _dispatch_to_json(dispatch, req):
    seller = dispatch.seller

    return {
        'seller_id': seller.id,
        'seller_name': seller.name,
        'wave_number': dispatch.wave_number,
        'position_number': dispatch.position_number,
        'status': dispatch.status,
        'status_label': 'Отправлено' if dispatch.status == RequestDispatch.STATUS_SENT else 'Ожидает отправки',
        'scheduled_at': dispatch.scheduled_at.strftime('%d.%m.%Y %H:%M') if dispatch.scheduled_at else '',
        'sent_at': dispatch.sent_at.strftime('%d.%m.%Y %H:%M') if dispatch.sent_at else '',
        'buyer_wa_link': _buyer_contact_link(seller.whatsapp, req),
    }


@csrf_exempt
def create_request(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'invalid method'}, status=405)

    _dispatch_due_requests()

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
    matched_sellers = list(sellers)

    dispatches = _build_dispatch_queue(req, matched_sellers)

    if matched_sellers:
        req.status = 'sent'
    else:
        req.status = 'no_sellers'
    req.save(update_fields=['status'])

    first_wave_dispatches = [
        dispatch for dispatch in dispatches
        if dispatch.wave_number == 1
    ]

    sellers_list = [
        _dispatch_to_json(dispatch, req)
        for dispatch in first_wave_dispatches
    ]

    all_sellers_list = [
        _dispatch_to_json(dispatch, req)
        for dispatch in dispatches
    ]

    seller_notifications = []
    for dispatch in first_wave_dispatches:
        seller = dispatch.seller
        seller_notifications.append({
            'seller': seller.name,
            'wa_link': _seller_notification_link(seller.whatsapp, req),
            'wave_number': dispatch.wave_number,
            'status': dispatch.status,
        })

    total_waves = 0
    if dispatches:
        total_waves = max(dispatch.wave_number for dispatch in dispatches)

    return JsonResponse({
        'status': 'ok',
        'id': req.id,
        'request_status': req.status,
        'matches': len(matched_sellers),
        'strategy': strategy_used,

        'wave_size': WAVE_SIZE,
        'wave_interval_minutes': WAVE_INTERVAL_MINUTES,
        'total_waves': total_waves,

        'message': (
            f"Заявка принята. Найдено продавцов: {len(matched_sellers)}. "
            f"Заявка отправляется продавцам волнами по {WAVE_SIZE} каждые "
            f"{WAVE_INTERVAL_MINUTES} минут."
        ),

        'sellers': sellers_list,
        'all_sellers': all_sellers_list,
        'seller_notifications': seller_notifications,
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

    all_countries = bool(data.get('all_countries'))
    all_brands = bool(data.get('all_brands'))
    all_models = bool(data.get('all_models'))
    all_categories = bool(data.get('all_categories'))

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

    if not all_categories and not category:
        return JsonResponse({'error': 'Выберите категорию или включите "Все категории"'}, status=400)

    if all_brands:
        all_models = True

    country = None
    brand = None
    car_model = None

    if not all_countries and country_id:
        country = Country.objects.filter(id=country_id).first()
        if not country:
            return JsonResponse({'error': 'Страна не найдена'}, status=400)

    if not all_brands and brand_id:
        brand = Brand.objects.filter(id=brand_id, transport_type=transport_type).first()
        if not brand:
            return JsonResponse({'error': 'Марка не найдена'}, status=400)

    if not all_brands and not all_models and model_id:
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
        category='' if all_categories else category,
        brand='' if all_brands else (brand.name if brand else ''),
        model='' if (all_brands or all_models) else (car_model.name if car_model else ''),
        country_fk=None if all_countries else country,
        brand_fk=None if all_brands else brand,
        model_fk=None if (all_brands or all_models) else car_model,
        all_countries=all_countries,
        all_brands=all_brands,
        all_models=all_models,
        all_categories=all_categories,
        is_active=True,
        is_paused=False,
    )

    if not all_categories and category:
        part_category, _ = PartCategory.objects.get_or_create(name=category)
        seller.selected_categories.add(part_category)

    if not all_countries and country:
        seller.selected_countries.add(country)

    if not all_brands and brand:
        seller.selected_brands.add(brand)

    if not all_brands and not all_models and car_model:
        seller.selected_models.add(car_model)

    return JsonResponse({
        'status': 'ok',
        'id': seller.id,
        'message': 'Продавец зарегистрирован'
    })


def seller_requests(request):
    _dispatch_due_requests()

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
            'match_id': m.id,
            'match_status': m.status,
            'id': r.id,
            'created_at': r.created_at.strftime('%d.%m.%Y %H:%M'),
            'transport_type': r.transport_type,
            'country': r.country,
            'city': r.city,
            'brand': r.brand,
            'model': r.model,
            'category': r.category,
            'article': r.article,
            'description': r.description,
            'phone': r.phone,
        })

    return JsonResponse({
        'seller': {
            'id': seller.id,
            'name': seller.name,
            'whatsapp': seller.whatsapp,
            'city': seller.city,
            'transport_type': seller.transport_type,
            'is_active': seller.is_active,
            'is_paused': seller.is_paused,
        },
        'period': period,
        'count': len(data),
        'requests': data,
    })


def seller_profile(request):
    seller_id = request.GET.get('seller_id')

    if not seller_id:
        return JsonResponse({'error': 'seller_id required'}, status=400)

    try:
        seller = Seller.objects.get(id=seller_id)
    except Seller.DoesNotExist:
        return JsonResponse({'error': 'seller not found'}, status=404)

    return JsonResponse({
        'id': seller.id,
        'name': seller.name,
        'whatsapp': seller.whatsapp,
        'city': seller.city,
        'transport_type': seller.transport_type,
        'category': seller.category,
        'brand': seller.brand,
        'model': seller.model,
        'dispatch_priority': seller.dispatch_priority,

        'selected_categories': [
            {'id': c.id, 'name': c.name}
            for c in seller.selected_categories.all()
        ],
        'selected_countries': [
            {'id': c.id, 'name': c.name}
            for c in seller.selected_countries.all()
        ],
        'selected_brands': [
            {'id': b.id, 'name': b.name}
            for b in seller.selected_brands.all()
        ],
        'selected_models': [
            {'id': m.id, 'name': m.name}
            for m in seller.selected_models.all()
        ],

        'all_countries': seller.all_countries,
        'all_brands': seller.all_brands,
        'all_models': seller.all_models,
        'all_categories': seller.all_categories,
        'is_active': seller.is_active,
        'is_paused': seller.is_paused,
    })


@csrf_exempt
def toggle_seller_pause(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'invalid method'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'invalid json'}, status=400)

    seller_id = data.get('seller_id')

    if not seller_id:
        return JsonResponse({'error': 'seller_id required'}, status=400)

    try:
        seller = Seller.objects.get(id=seller_id)
    except Seller.DoesNotExist:
        return JsonResponse({'error': 'seller not found'}, status=404)

    seller.is_paused = not seller.is_paused
    seller.save()

    return JsonResponse({
        'status': 'ok',
        'seller_id': seller.id,
        'is_paused': seller.is_paused,
    })


@csrf_exempt
def update_seller_profile(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'invalid method'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'invalid json'}, status=400)

    seller_id = data.get('seller_id')

    if not seller_id:
        return JsonResponse({'error': 'seller_id required'}, status=400)

    try:
        seller = Seller.objects.get(id=seller_id)
    except Seller.DoesNotExist:
        return JsonResponse({'error': 'seller not found'}, status=404)

    seller.whatsapp = data.get('whatsapp', seller.whatsapp)
    seller.city = data.get('city', seller.city)
    seller.category = data.get('category', seller.category)
    seller.brand = data.get('brand', seller.brand)

    seller.all_categories = bool(data.get('all_categories', seller.all_categories))
    seller.all_brands = bool(data.get('all_brands', seller.all_brands))
    seller.all_countries = bool(data.get('all_countries', seller.all_countries))
    seller.all_models = bool(data.get('all_models', seller.all_models))

    selected_category_ids = data.get('selected_category_ids')
    selected_country_ids = data.get('selected_country_ids')
    selected_brand_ids = data.get('selected_brand_ids')
    selected_model_ids = data.get('selected_model_ids')

    if selected_category_ids is not None:
        seller.selected_categories.set(
            PartCategory.objects.filter(id__in=selected_category_ids)
        )

    if selected_country_ids is not None:
        seller.selected_countries.set(
            Country.objects.filter(id__in=selected_country_ids)
        )

    if selected_brand_ids is not None:
        seller.selected_brands.set(
            Brand.objects.filter(id__in=selected_brand_ids)
        )

    if selected_model_ids is not None:
        seller.selected_models.set(
            CarModel.objects.filter(id__in=selected_model_ids)
        )

    if seller.all_categories:
        seller.category = ''

    if seller.all_brands:
        seller.brand = ''
        seller.brand_fk = None
        seller.model = ''
        seller.model_fk = None
        seller.all_models = True

    if seller.all_countries:
        seller.country_fk = None

    seller.save()

    return JsonResponse({
        'status': 'ok',
        'seller_id': seller.id,
    })


@csrf_exempt
def update_match_status(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'invalid method'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'invalid json'}, status=400)

    match_id = data.get('match_id')
    status = data.get('status')

    allowed_statuses = [
        'prepared',
        'viewed',
        'contacted',
        'rejected',
        'archived',
    ]

    if not match_id:
        return JsonResponse({'error': 'match_id required'}, status=400)

    if status not in allowed_statuses:
        return JsonResponse({'error': 'invalid status'}, status=400)

    try:
        match = Match.objects.get(id=match_id)
    except Match.DoesNotExist:
        return JsonResponse({'error': 'match not found'}, status=404)

    match.status = status
    match.save()

    return JsonResponse({
        'status': 'ok',
        'match_id': match.id,
        'match_status': match.status,
    })