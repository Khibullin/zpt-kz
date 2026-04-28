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
    BroadcastSettings,
)

WAVE_SIZE = 10
WAVE_INTERVAL_MINUTES = 5


def countries_list(request):
    countries = Country.objects.all().order_by('name')
    return JsonResponse(
        [{'id': c.id, 'name': c.name} for c in countries],
        safe=False
    )


def brands_by_country(request):
    country_id = request.GET.get('country_id')
    transport_type = request.GET.get('transport_type')

    brands = Brand.objects.all().order_by('name')

    if country_id:
        brands = brands.filter(country_id=country_id)

    if transport_type:
        brands = brands.filter(transport_type=transport_type)

    data = [{
        'id': b.id,
        'name': b.name,
        'country_id': b.country_id,
        'transport_type': b.transport_type
    } for b in brands]

    return JsonResponse(data, safe=False)


def models_by_brand(request):
    brand_id = request.GET.get('brand_id')
    transport_type = request.GET.get('transport_type')

    models = CarModel.objects.all().order_by('name')

    if brand_id:
        models = models.filter(brand_id=brand_id)

    if transport_type:
        models = models.filter(transport_type=transport_type)

    data = [{
        'id': m.id,
        'name': m.name,
        'brand_id': m.brand_id,
        'transport_type': m.transport_type
    } for m in models]

    return JsonResponse(data, safe=False)


def part_categories_list(request):
    categories = PartCategory.objects.all().order_by('name')
    return JsonResponse(
        [{'id': c.id, 'name': c.name} for c in categories],
        safe=False
    )


# -------------------------
# BROADCAST CONTROL
# -------------------------

def _base_sellers_queryset(req):
    settings = BroadcastSettings.load()

    base = Seller.objects.filter(
        is_active=True,
        is_paused=False,
        transport_type=req.transport_type
    )

    if settings.emergency_stop:
        return Seller.objects.none()

    if settings.mode == 'off':
        return Seller.objects.none()

    if settings.mode == 'test':
        return base.filter(
            is_test_seller=True
        ).distinct()

    if settings.mode == 'live':
        return base.filter(
            receive_requests=True
        ).distinct()

    return Seller.objects.none()


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
        {'city': True, 'country': True, 'brand': True, 'model': True},
        {'city': True, 'country': True, 'brand': True, 'model': False},
        {'city': True, 'country': False, 'brand': False, 'model': False},
        {'city': False, 'country': False, 'brand': False, 'model': False},
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

        qs = qs.order_by('dispatch_priority', 'id')

        if qs.exists():
            return qs, 'matched'

    return Seller.objects.none(), 'no_match'


def _normalize_whatsapp(phone):
    return ''.join(ch for ch in str(phone or '') if ch.isdigit())


def _seller_notification_text(req):
    return (
        f"Новая заявка от клиента на автозапчасть\n\n"
        f"Марка: {req.brand}\n"
        f"Модель: {req.model}\n"
        f"Категория: {req.category}\n\n"
        f"Комментарий клиента:\n"
        f"{req.description or '-'}\n\n"
        f"Телефон клиента: {req.phone}\n\n"
        f"Пожалуйста, свяжитесь с клиентом и предложите наличие, цену и сроки поставки.\n\n"
        f"Личный кабинет / просмотр заявок / отписаться:\n"
        f"https://zpt.kz\n\n"
        f"По всем вопросам:\n"
        f"WhatsApp +7 771 360 7040"
    )


def _buyer_to_seller_text(req):
    return (
        f"Здравствуйте! По моей заявке №{req.id}\n\n"
        f"{req.brand} {req.model}\n"
        f"{req.category}\n\n"
        f"{req.description or '-'}"
    )


def _seller_notification_link(phone, req):
    return f"https://wa.me/{_normalize_whatsapp(phone)}?text={quote(_seller_notification_text(req))}"


def _buyer_contact_link(phone, req):
    return f"https://wa.me/{_normalize_whatsapp(phone)}?text={quote(_buyer_to_seller_text(req))}"


def _mark_dispatch_sent(dispatch):
    now = timezone.now()

    Match.objects.get_or_create(
        request=dispatch.request,
        seller=dispatch.seller,
        defaults={
            'status': 'prepared',
            'sent_at': now
        }
    )

    if dispatch.status != RequestDispatch.STATUS_SENT:
        dispatch.status = RequestDispatch.STATUS_SENT
        dispatch.sent_at = now
        dispatch.save(update_fields=['status', 'sent_at'])


def _dispatch_due_requests():
    due = RequestDispatch.objects.filter(
        status=RequestDispatch.STATUS_QUEUED,
        scheduled_at__lte=timezone.now()
    ).order_by('scheduled_at','position_number')

    for d in due:
        _mark_dispatch_sent(d)


def _build_dispatch_queue(req, sellers):
    dispatches=[]
    now=timezone.now()

    for idx,seller in enumerate(sellers,start=1):
        wave=((idx-1)//WAVE_SIZE)+1

        scheduled_at = now + timedelta(
            minutes=(wave-1)*WAVE_INTERVAL_MINUTES
        )

        d,_=RequestDispatch.objects.get_or_create(
            request=req,
            seller=seller,
            defaults=dict(
                wave_number=wave,
                position_number=idx,
                status=RequestDispatch.STATUS_QUEUED,
                scheduled_at=scheduled_at
            )
        )

        dispatches.append(d)

    for d in dispatches:
        if d.wave_number==1:
            _mark_dispatch_sent(d)

    return dispatches


def _dispatch_to_json(dispatch, req):
    s=dispatch.seller

    return {
        'seller_id':s.id,
        'seller_name':s.name,
        'wave_number':dispatch.wave_number,
        'status':dispatch.status,
        'buyer_wa_link':_buyer_contact_link(
            s.whatsapp,
            req
        )
    }


@csrf_exempt
def create_request(request):

    if request.method!='POST':
        return JsonResponse(
            {'error':'invalid method'},
            status=405
        )

    _dispatch_due_requests()

    data=json.loads(request.body)

    req=Request.objects.create(
        transport_type=data.get('transport_type'),
        country=data.get('country',''),
        brand=data.get('brand',''),
        model=data.get('model',''),
        category=data.get('category',''),
        article=data.get('article',''),
        description=data.get('description',''),
        city=data.get('city',''),
        phone=data.get('phone',''),
    )

    sellers,strategy=_find_matching_sellers(req)

    matched=list(sellers)

    dispatches=_build_dispatch_queue(
        req,
        matched
    )

    req.status='sent' if matched else 'no_sellers'
    req.save(update_fields=['status'])

    first_wave=[
        d for d in dispatches
        if d.wave_number==1
    ]

    seller_notifications=[
        {
            'seller':d.seller.name,
            'wa_link':_seller_notification_link(
                d.seller.whatsapp,
                req
            )
        }
        for d in first_wave
    ]

    return JsonResponse({
        'status':'ok',
        'id':req.id,
        'matches':len(matched),
        'strategy':strategy,
        'wave_size':WAVE_SIZE,
        'wave_interval_minutes':WAVE_INTERVAL_MINUTES,
        'message':
            f'Заявка отправляется волнами по '
            f'{WAVE_SIZE} каждые '
            f'{WAVE_INTERVAL_MINUTES} минут.',
        'sellers':[
            _dispatch_to_json(d,req)
            for d in first_wave
        ],
        'all_sellers':[
            _dispatch_to_json(d,req)
            for d in dispatches
        ],
        'seller_notifications':seller_notifications
    })


@csrf_exempt
def create_seller(request):

    if request.method!='POST':
        return JsonResponse(
            {'error':'invalid method'},
            status=405
        )

    data=json.loads(request.body)

    seller=Seller.objects.create(
        name=data.get('name'),
        whatsapp=data.get('whatsapp'),
        transport_type=data.get('transport_type'),
        city=data.get('city'),
        category=data.get('category',''),
        is_active=True,
        is_paused=False
    )

    return JsonResponse({
        'status':'ok',
        'id':seller.id
    })


def seller_requests(request):
    seller_id=request.GET.get('seller_id')

    seller=Seller.objects.get(
        id=seller_id
    )

    matches=Match.objects.filter(
        seller=seller
    ).select_related('request')

    data=[]

    for m in matches:
        r=m.request

        data.append({
            'id':r.id,
            'brand':r.brand,
            'model':r.model,
            'category':r.category,
            'phone':r.phone
        })

    return JsonResponse({
        'count':len(data),
        'requests':data
    })


def seller_profile(request):
    seller_id=request.GET.get('seller_id')

    seller=Seller.objects.get(
        id=seller_id
    )

    return JsonResponse({
        'id':seller.id,
        'name':seller.name,
        'whatsapp':seller.whatsapp,
        'city':seller.city
    })


@csrf_exempt
def toggle_seller_pause(request):
    data=json.loads(request.body)

    seller=Seller.objects.get(
        id=data['seller_id']
    )

    seller.is_paused=not seller.is_paused
    seller.save()

    return JsonResponse({
        'status':'ok',
        'is_paused':seller.is_paused
    })


@csrf_exempt
def update_seller_profile(request):
    data=json.loads(request.body)

    seller=Seller.objects.get(
        id=data['seller_id']
    )

    seller.whatsapp=data.get(
        'whatsapp',
        seller.whatsapp
    )

    seller.city=data.get(
        'city',
        seller.city
    )

    seller.save()

    return JsonResponse({
        'status':'ok'
    })


@csrf_exempt
def update_match_status(request):
    data=json.loads(request.body)

    match=Match.objects.get(
        id=data['match_id']
    )

    match.status=data['status']
    match.save()

    return JsonResponse({
        'status':'ok'
    })