import io
import json
import logging
import os
import re
import uuid
import urllib.error
import urllib.request
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from PIL import Image, ImageOps

from .models import (
    Request,
    RequestPhoto,
    Country,
    Brand,
    CarModel,
    PartCategory,
    Seller,
    Match,
    RequestDispatch,
    BroadcastSettings,
    WhatsAppMessageLog,
)


WAVE_SIZE = 20
WAVE_INTERVAL_MINUTES = 5
TEMP_SELLER_PASSWORD = 'zpt2026'
logger = logging.getLogger(__name__)


def _normalize_whatsapp(phone):
    return ''.join(ch for ch in str(phone or '') if ch.isdigit())

def _format_whatsapp_display(phone):
    digits = _normalize_whatsapp(phone)

    if digits.startswith('8') and len(digits) == 11:
        digits = '7' + digits[1:]

    if digits.startswith('7') and len(digits) == 11:
        return (
            f"+{digits[0]} "
            f"{digits[1:4]} "
            f"{digits[4:7]} "
            f"{digits[7:11]}"
        )

    return digits


def _wa_template_param(value):
    text = str(value or '-')

    text = text.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
    text = re.sub(r'\s+', ' ', text)

    text = text.strip()
    text = text[:500]

    return {
        'type': 'text',
        'text': text if text else '-',
    }


def _public_media_url(relative_url):
    base = os.getenv('PUBLIC_BASE_URL', 'https://zpt.kz').rstrip('/')
    path = relative_url if relative_url.startswith('/') else f'/{relative_url}'
    return f'{base}{path}'


def _request_page_url(req):
    base = os.getenv('PUBLIC_BASE_URL', 'https://zpt.kz').rstrip('/')
    return f'{base}/my-request/{req.id}/'


def _description_with_request_link(req):
    page_url = _request_page_url(req)
    description = (req.description or '-').strip()
    combined = f'{description} Смотреть заявку: {page_url}'
    return combined[:500]


def _seller_template_body_params(req):
    return [
        _wa_template_param(req.id),
        _wa_template_param(req.brand),
        _wa_template_param(req.model),
        _wa_template_param(req.category),
        _wa_template_param(req.city),
        _wa_template_param(_description_with_request_link(req)),
        _wa_template_param(_format_whatsapp_display(req.phone)),
    ]


def _buyer_template_body_params(req, sellers_count=0):
    public_base_url = os.getenv('PUBLIC_BASE_URL', 'https://zpt.kz').rstrip('/')
    request_url = f'{public_base_url}/my-request/{req.id}/'
    return [
        _wa_template_param(req.id),
        _wa_template_param(req.brand),
        _wa_template_param(req.model),
        _wa_template_param(req.category),
        _wa_template_param(sellers_count),
        _wa_template_param(request_url),
        _wa_template_param(req.city),
    ]


def _is_probably_image(uploaded_file):
    content_type = getattr(uploaded_file, 'content_type', '') or ''
    if content_type.startswith('image/'):
        return True

    name = (getattr(uploaded_file, 'name', '') or '').lower()
    return name.endswith((
        '.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif', '.bmp',
    ))


def _process_and_save_request_photo(req, uploaded_file):
    try:
        uploaded_file.seek(0)
        img = Image.open(uploaded_file)
        img = ImageOps.exif_transpose(img)
        img = img.convert('RGB')

        max_size = 1200
        width, height = img.size
        if max(width, height) > max_size:
            ratio = max_size / max(width, height)
            img = img.resize(
                (int(width * ratio), int(height * ratio)),
                Image.Resampling.LANCZOS,
            )

        buffer = io.BytesIO()
        img.save(buffer, format='WEBP', quality=80)
        buffer.seek(0)

        filename = f'{uuid.uuid4().hex}.webp'
        photo = RequestPhoto(request=req)
        photo.image.save(filename, ContentFile(buffer.read()), save=True)
        return photo
    except Exception as exc:
        logger.warning(
            'WebP conversion failed for request #%s, saving original: %s',
            req.id,
            exc,
        )
        uploaded_file.seek(0)
        extension = os.path.splitext(uploaded_file.name or '')[1].lower()
        if extension not in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif'):
            extension = '.jpg'
        filename = f'{uuid.uuid4().hex}{extension}'
        photo = RequestPhoto(request=req)
        photo.image.save(filename, uploaded_file, save=True)
        return photo


def _save_request_photos(req, uploaded_files):
    saved = []

    print(
        f'REQUEST_PHOTO_SAVE start: request_id={req.id} '
        f'incoming_files={len(uploaded_files)}',
    )

    if not uploaded_files:
        return saved

    media_root = settings.MEDIA_ROOT
    os.makedirs(media_root, exist_ok=True)
    os.makedirs(media_root / 'request_photos', exist_ok=True)

    for uploaded in uploaded_files:
        if not _is_probably_image(uploaded):
            logger.warning(
                'Skipped non-image upload for request #%s: %s',
                req.id,
                getattr(uploaded, 'name', '-'),
            )
            continue

        try:
            photo = _process_and_save_request_photo(req, uploaded)
            saved.append(photo)
            print(
                f'REQUEST_PHOTO_SAVE ok: request_id={req.id} '
                f'file={photo.image.name}',
            )
        except Exception as exc:
            logger.exception(
                'Failed to save request photo for request #%s: %s',
                req.id,
                exc,
            )

    print(
        f'REQUEST_PHOTO_SAVE done: request_id={req.id} '
        f'saved={len(saved)}/{len(uploaded_files)}',
    )
    return saved


def _collect_request_uploads(request):
    """Collect uploaded images from all common FormData keys and any FILES entries."""
    collected = []
    seen_ids = set()

    preferred_keys = ('photos', 'photo', 'images', 'image', 'files')
    for key in preferred_keys:
        for uploaded in request.FILES.getlist(key):
            object_id = id(uploaded)
            if object_id not in seen_ids:
                seen_ids.add(object_id)
                collected.append(uploaded)

    if not collected:
        for key in request.FILES:
            for uploaded in request.FILES.getlist(key):
                object_id = id(uploaded)
                if object_id not in seen_ids:
                    seen_ids.add(object_id)
                    collected.append(uploaded)

    return collected


def _post_fields_from_request(request):
    return {
        'transport_type': request.POST.get('transport_type'),
        'country': request.POST.get('country', ''),
        'brand': request.POST.get('brand', ''),
        'model': request.POST.get('model', ''),
        'category': request.POST.get('category', ''),
        'article': request.POST.get('article', ''),
        'description': request.POST.get('description', ''),
        'city': request.POST.get('city', ''),
        'search_scope': request.POST.get('search_scope', 'city'),
        'selected_cities': request.POST.getlist('selected_cities'),
        'phone': request.POST.get('phone', ''),
    }


def _parse_create_request_data(request):
    content_type = (request.content_type or '').lower()
    uploaded_photos = _collect_request_uploads(request)
    file_keys = list(request.FILES.keys())
    is_multipart = (
        'multipart/form-data' in content_type
        or bool(file_keys)
    )

    print(
        'CREATE REQUEST PARSE:',
        f'content_type={content_type!r}',
        f'file_keys={file_keys}',
        f'photos_in_request={len(uploaded_photos)}',
        f'is_multipart={is_multipart}',
    )

    if is_multipart:
        return _post_fields_from_request(request), uploaded_photos, 'multipart'

    if request.body and 'application/json' in content_type:
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            data = None

        if isinstance(data, dict):
            selected_cities = data.get('selected_cities', [])
            if isinstance(selected_cities, str):
                selected_cities = [
                    city.strip()
                    for city in selected_cities.split(',')
                    if city.strip()
                ]

            print(
                'CREATE REQUEST PARSE: JSON mode — photos cannot be uploaded',
            )

            return {
                'transport_type': data.get('transport_type'),
                'country': data.get('country', ''),
                'brand': data.get('brand', ''),
                'model': data.get('model', ''),
                'category': data.get('category', ''),
                'article': data.get('article', ''),
                'description': data.get('description', ''),
                'city': data.get('city', ''),
                'search_scope': data.get('search_scope', 'city'),
                'selected_cities': selected_cities,
                'phone': data.get('phone', ''),
            }, [], 'json'

    print('CREATE REQUEST PARSE: fallback POST mode')
    return _post_fields_from_request(request), uploaded_photos, 'post'


def send_whatsapp_template(
    to_phone,
    req,
    seller_name='',
    *,
    template_name=None,
    body_parameters=None,
    include_image_header=None,
):
    phone_number_id = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
    access_token = os.getenv('WHATSAPP_ACCESS_TOKEN')
    template_name = template_name or os.getenv(
        'WHATSAPP_TEMPLATE_NAME',
        'zpt_request_notification',
    )
    template_lang = os.getenv('WHATSAPP_TEMPLATE_LANG', 'ru')

    to_phone = _normalize_whatsapp(to_phone)

    if not phone_number_id or not access_token:
        error_text = 'WhatsApp ENV variables are not configured'

        WhatsAppMessageLog.objects.create(
            request_id=req.id,
            seller_name=seller_name or '-',
            phone_clean=to_phone or '-',
            is_success=False,
            status_text='env_error',
            message_id='',
            error_text=error_text,
        )

        return {
            'ok': False,
            'status_code': None,
            'error': error_text,
        }

    if not to_phone:
        error_text = 'Recipient WhatsApp phone is empty'

        WhatsAppMessageLog.objects.create(
            request_id=req.id,
            seller_name=seller_name or '-',
            phone_clean='-',
            is_success=False,
            status_text='phone_error',
            message_id='',
            error_text=error_text,
        )

        return {
            'ok': False,
            'status_code': None,
            'error': error_text,
        }

    url = f'https://graph.facebook.com/v20.0/{phone_number_id}/messages'

    if include_image_header is None:
        include_image_header = (
            os.getenv('WHATSAPP_TEMPLATE_HAS_IMAGE_HEADER', 'false').lower()
            == 'true'
        )

    components = []

    if include_image_header:
        first_photo = req.photos.order_by('created_at').first()
        if first_photo and first_photo.image:
            components.append({
                'type': 'header',
                'parameters': [
                    {
                        'type': 'image',
                        'image': {
                            'link': _public_media_url(first_photo.image.url),
                        },
                    },
                ],
            })

    components.append({
        'type': 'body',
        'parameters': body_parameters or _seller_template_body_params(req),
    })

    payload = {
        'messaging_product': 'whatsapp',
        'to': to_phone,
        'type': 'template',
        'template': {
            'name': template_name,
            'language': {
                'code': template_lang,
            },
            'components': components,
        },
    }

    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')

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
        with urllib.request.urlopen(http_request, timeout=20) as response:
            response_body = response.read().decode('utf-8')
            response_json = {}

            try:
                response_json = json.loads(response_body)
            except Exception:
                response_json = {}

            messages = response_json.get('messages') or []
            message_id = messages[0].get('id', '') if messages else ''
            is_ok = 200 <= response.status < 300

            WhatsAppMessageLog.objects.create(
                request_id=req.id,
                seller_name=seller_name or '-',
                phone_clean=to_phone,
                is_success=is_ok,
                status_text='sent' if is_ok else 'error',
                message_id=message_id,
                error_text='' if is_ok else response_body,
            )

            return {
                'ok': is_ok,
                'status_code': response.status,
                'response': response_json or response_body,
                'message_id': message_id,
                'error': None if is_ok else (response_json or response_body),
            }

    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')

        WhatsAppMessageLog.objects.create(
            request_id=req.id,
            seller_name=seller_name or '-',
            phone_clean=to_phone,
            is_success=False,
            status_text='http_error',
            message_id='',
            error_text=error_body,
        )

        return {
            'ok': False,
            'status_code': e.code,
            'error': error_body,
        }

    except Exception as e:
        error_text = str(e)

        WhatsAppMessageLog.objects.create(
            request_id=req.id,
            seller_name=seller_name or '-',
            phone_clean=to_phone,
            is_success=False,
            status_text='error',
            message_id='',
            error_text=error_text,
        )

        return {
            'ok': False,
            'status_code': None,
            'error': error_text,
        }

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

    data = [
        {
            'id': b.id,
            'name': b.name,
            'country_id': b.country_id,
            'transport_type': b.transport_type,
        }
        for b in brands
    ]

    return JsonResponse(data, safe=False)


def models_by_brand(request):
    brand_id = request.GET.get('brand_id')
    transport_type = request.GET.get('transport_type')

    models = CarModel.objects.all().order_by('name')

    if brand_id:
        models = models.filter(brand_id=brand_id)

    if transport_type:
        models = models.filter(transport_type=transport_type)

    data = [
        {
            'id': m.id,
            'name': m.name,
            'brand_id': m.brand_id,
            'transport_type': m.transport_type,
        }
        for m in models
    ]

    return JsonResponse(data, safe=False)


def part_categories_list(request):
    categories = PartCategory.objects.all().order_by('name')
    return JsonResponse(
        [{'id': c.id, 'name': c.name} for c in categories],
        safe=False
    )


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
        return base.filter(is_test_seller=True).distinct()

    if settings.mode == 'live':
        return base.filter(receive_requests=True).distinct()

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

    search_scope = req.search_scope or 'city'

    selected_cities = []

    if req.selected_cities:
        selected_cities = [
            city.strip()
            for city in req.selected_cities.split(',')
            if city.strip()
        ]

    strategies = [
        {'country': True, 'brand': True, 'model': True},
        {'country': True, 'brand': True, 'model': False},
        {'country': True, 'brand': False, 'model': False},
    ]

    for strategy in strategies:
        qs = base_qs

        if search_scope == 'city':
            if req.city:
                qs = qs.filter(city=req.city)

        elif search_scope == 'custom':
            if selected_cities:
                qs = qs.filter(city__in=selected_cities)

        if strategy['country']:
            qs = _apply_country_filter(qs, req)

        if strategy['brand']:
            qs = _apply_brand_filter(qs, req)

        if strategy['model']:
            qs = _apply_model_filter(qs, req)

        qs = qs.order_by(
            'dispatch_priority',
            'id'
        ).distinct()

        if qs.exists():
            return qs, 'matched'

    if search_scope in ['city', 'custom']:

        for strategy in strategies:
            qs = base_qs

            if strategy['country']:
                qs = _apply_country_filter(qs, req)

            if strategy['brand']:
                qs = _apply_brand_filter(qs, req)

            if strategy['model']:
                qs = _apply_model_filter(qs, req)

            qs = qs.order_by(
                'dispatch_priority',
                'id'
            ).distinct()

            if qs.exists():
                return qs, 'fallback_kazakhstan'

    return Seller.objects.none(), 'no_match'

def _seller_notification_text(req):
    return (
        f"Новая заявка #{req.id} от клиента на автозапчасть\n\n"
        f"Марка: {req.brand}\n"
        f"Модель: {req.model}\n"
        f"Категория: {req.category}\n"
        f"Город: {req.city}\n\n"
        f"Комментарий клиента:\n"
        f"{req.description or '-'}\n\n"
        f"Телефон клиента: {_format_whatsapp_display(req.phone)}\n"
        f"WhatsApp клиента:\n"
        f"https://wa.me/{_normalize_whatsapp(req.phone)}\n\n"
        f"Пожалуйста, свяжитесь с клиентом и предложите наличие, цену и сроки поставки.\n\n"
        f"Личный кабинет / просмотр заявок / отписаться:\n"
        f"https://zpt.kz\n\n"
        f"По всем вопросам:\n"
        f"WhatsApp +7 771 360 7040"
    )
def _buyer_to_seller_text(req):
    return (
        f"Здравствуйте!\n\n"
        f"Пишу по заявке с ZPT.kz №{req.id}.\n\n"
        f"Автомобиль: {req.brand} {req.model}\n"
        f"Категория: {req.category}\n"
        f"Город: {req.city}\n\n"
        f"Нужная запчасть:\n"
        f"{req.description or '-'}\n\n"
        f"Подскажите, пожалуйста, есть ли в наличии, цена и срок?"
    )


def _seller_notification_link(phone, req):
    return (
        f"https://api.whatsapp.com/send"
        f"?phone={_normalize_whatsapp(phone)}"
        f"&text={quote(_seller_notification_text(req))}"
    )


def _buyer_contact_link(phone, req):
    return (
        f"https://api.whatsapp.com/send"
        f"?phone={_normalize_whatsapp(phone)}"
        f"&text={quote(_buyer_to_seller_text(req))}"
    )


def _send_dispatch(dispatch):
    now = timezone.now()

    match, _ = Match.objects.get_or_create(
        request=dispatch.request,
        seller=dispatch.seller,
        defaults={
            'status': 'prepared',
            'sent_at': now,
        }
    )

    try:
        wa_result = send_whatsapp_template(
            dispatch.seller.whatsapp,
            dispatch.request,
            dispatch.seller.name,
        )

        if wa_result.get('ok'):
            match.status = 'sent'
            match.sent_at = now

            dispatch.status = RequestDispatch.STATUS_SENT
            dispatch.sent_at = now

            match.save(update_fields=['status', 'sent_at'])
            dispatch.save(update_fields=['status', 'sent_at'])

        else:
            match.status = 'error'
            match.save(update_fields=['status'])

    except Exception:
        match.status = 'error'
        match.save(update_fields=['status'])


def _dispatch_due_requests():
    due = RequestDispatch.objects.filter(
        status=RequestDispatch.STATUS_QUEUED,
        scheduled_at__lte=timezone.now()
    ).select_related(
        'request',
        'seller'
    ).order_by('scheduled_at', 'position_number')

    for dispatch in due:
        _send_dispatch(dispatch)


def _build_dispatch_queue(req, sellers):
    dispatches = []
    now = timezone.now()
    settings = BroadcastSettings.load()

    wave_size = settings.wave_size or WAVE_SIZE
    wave_interval = settings.wave_interval_minutes or WAVE_INTERVAL_MINUTES

    for index, seller in enumerate(sellers, start=1):
        wave_number = ((index - 1) // wave_size) + 1

        scheduled_at = now + timedelta(
            minutes=(wave_number - 1) * wave_interval
        )

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
            _send_dispatch(dispatch)

    return dispatches

def _dispatch_to_json(dispatch, req):
    seller = dispatch.seller

    seller_name = (
        seller.name.strip()
        if seller.name
        else f'Продавец #{seller.id}'
    )

    whatsapp_status = 'pending'

    try:
        match = Match.objects.filter(
            request=req,
            seller=seller
        ).first()

        if match and match.status == 'sent':
            whatsapp_status = 'sent'
        elif match and match.status == 'error':
            whatsapp_status = 'error'

    except Exception:
        pass

    return {
        'seller_id': seller.id,
        'seller_name': seller_name,
        'seller_catalog_url': f'https://zpt-kz-backend.onrender.com/api/parts-seller/{seller.id}/',
        'wave_number': dispatch.wave_number,
        'status': dispatch.status,
        'whatsapp_status': whatsapp_status,
        'buyer_wa_link': _buyer_contact_link(
            seller.whatsapp,
            req
        ),
    }


@csrf_exempt
def create_request(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'invalid method'}, status=405)

    print('CREATE REQUEST CALLED (WAVES MODE)')

    try:
        _dispatch_due_requests()

        data, uploaded_photos, upload_mode = _parse_create_request_data(request)

        transport_type = data.get('transport_type') or 'car'
        if transport_type not in ('car', 'truck'):
            return JsonResponse(
                {'error': 'Некорректный тип транспорта'},
                status=400,
            )

        selected_cities = data.get('selected_cities') or []
        if isinstance(selected_cities, str):
            selected_cities = [
                city.strip()
                for city in selected_cities.split(',')
                if city.strip()
            ]

        req = Request.objects.create(
            transport_type=transport_type,
            country=data.get('country', ''),
            brand=data.get('brand', ''),
            model=data.get('model', ''),
            category=data.get('category', ''),
            article=data.get('article', ''),
            description=data.get('description', ''),
            city=data.get('city', ''),
            search_scope=data.get('search_scope', 'city'),
            selected_cities=','.join(selected_cities),
            phone=data.get('phone', ''),
        )

        saved_photos = _save_request_photos(req, uploaded_photos)
        photos_saved = len(saved_photos)

        if uploaded_photos and photos_saved == 0:
            logger.error(
                'Request #%s received %s photo(s) but none were saved',
                req.id,
                len(uploaded_photos),
            )

        sellers, strategy = _find_matching_sellers(req)
        matched = list(sellers)
        sellers_count = len(matched)

        print('TOTAL MATCHED SELLERS:', sellers_count)

        dispatches = _build_dispatch_queue(
            req,
            matched
        )

        try:
            buyer_template = os.getenv('WHATSAPP_BUYER_TEMPLATE_NAME', 'zpt_buyer_request_re')
            if buyer_template:
                send_whatsapp_template(
                    req.phone,
                    req,
                    'Покупатель',
                    template_name=buyer_template,
                    body_parameters=_buyer_template_body_params(
                        req,
                        sellers_count=sellers_count,
                    ),
                )
        except Exception as exc:
            logger.error(
                'Buyer WhatsApp notification failed for request #%s: %s',
                req.id,
                exc,
                exc_info=True,
            )

        req.status = 'sent' if matched else 'no_sellers'
        req.save(update_fields=['status'])

        seller_notifications = [
            _dispatch_to_json(
                dispatch,
                req
            )
            for dispatch in dispatches
        ]

        return JsonResponse({
            'status': 'ok',
            'id': req.id,
            'matches': len(matched),
            'strategy': strategy,
            'message': 'Заявка поставлена в очередь согласно настройкам рассылки',
            'photo_view_url': _request_page_url(req),
            'upload_mode': upload_mode,
            'files_keys': list(request.FILES.keys()),
            'photos_received': len(uploaded_photos),
            'photos_saved': photos_saved,
            'seller_notifications': seller_notifications,
        })

    except Exception as exc:
        logger.exception('create_request failed')
        print('CREATE REQUEST ERROR:', str(exc))
        return JsonResponse(
            {'error': 'Не удалось создать заявку. Попробуйте ещё раз.'},
            status=500,
        )


REQUEST_STATUS_LABELS = {
    'new': 'Новая',
    'sent': 'Отправлена продавцам',
    'no_sellers': 'Продавцы не найдены',
}


def view_request_status(request, req_id):
    photo_qs = RequestPhoto.objects.order_by('created_at')
    req = get_object_or_404(
        Request.objects.prefetch_related(
            Prefetch('photos', queryset=photo_qs),
        ),
        pk=req_id,
    )
    photos = list(req.photos.all())

    return render(request, 'request_status.html', {
        'req': req,
        'photos': photos,
        'photos_count': len(photos),
        'status_label': REQUEST_STATUS_LABELS.get(req.status, req.status),
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
    whatsapp = _normalize_whatsapp(data.get('whatsapp'))
    password = data.get('password') or ''
    password_confirm = data.get('password_confirm') or ''
    transport_type = data.get('transport_type')
    city = data.get('city') or ''
    category = data.get('category') or ''

    selected_category_ids = data.get('selected_category_ids') or []
    selected_country_ids = data.get('selected_country_ids') or []
    selected_brand_ids = data.get('selected_brand_ids') or []
    selected_model_ids = data.get('selected_model_ids') or []

    all_categories = bool(data.get('all_categories', False))
    all_countries = bool(data.get('all_countries', False))
    all_brands = bool(data.get('all_brands', False))
    all_models = bool(data.get('all_models', False))

    if not name:
        return JsonResponse({'error': 'Укажите название продавца'}, status=400)

    if not whatsapp:
        return JsonResponse({'error': 'Укажите WhatsApp'}, status=400)

    if transport_type not in ['car', 'truck']:
        return JsonResponse({'error': 'Некорректный тип транспорта'}, status=400)

    if not password:
        return JsonResponse({'error': 'Укажите пароль'}, status=400)

    if len(password) < 6:
        return JsonResponse({'error': 'Пароль должен быть не короче 6 символов'}, status=400)

    if password != password_confirm:
        return JsonResponse({'error': 'Пароли не совпадают'}, status=400)

    if Seller.objects.filter(whatsapp=whatsapp).exists():
        return JsonResponse({'error': 'Продавец с таким WhatsApp уже зарегистрирован'}, status=400)

    seller = Seller.objects.create(
        name=name,
        whatsapp=whatsapp,
        password_hash=make_password(password),
        must_change_password=False,
        transport_type=transport_type,
        city=city,
        category=category,
        is_active=True,
        is_paused=False,
        receive_requests=False,
        is_test_seller=False,
        all_categories=all_categories,
        all_countries=all_countries,
        all_brands=all_brands,
        all_models=all_models,
    )

    if all_categories:
        seller.selected_categories.clear()
    else:
        seller.selected_categories.set(selected_category_ids)

    if all_countries:
        seller.selected_countries.clear()
    else:
        seller.selected_countries.set(selected_country_ids)

    if all_brands:
        seller.selected_brands.clear()
    else:
        seller.selected_brands.set(selected_brand_ids)

    if all_models:
        seller.selected_models.clear()
    else:
        seller.selected_models.set(selected_model_ids)

    return JsonResponse({
        'status': 'ok',
        'id': seller.id,
        'message': 'Продавец зарегистрирован',
    })

@csrf_exempt
def seller_login(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'invalid method'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'invalid json'}, status=400)

    whatsapp = _normalize_whatsapp(data.get('whatsapp'))
    password = data.get('password') or ''

    try:
        seller = Seller.objects.get(whatsapp=whatsapp)
    except Seller.DoesNotExist:
        return JsonResponse({'error': 'Неверный WhatsApp или пароль'}, status=400)

    if not seller.password_hash:
        seller.password_hash = make_password(TEMP_SELLER_PASSWORD)
        seller.must_change_password = True
        seller.save(update_fields=['password_hash', 'must_change_password'])

    if not check_password(password, seller.password_hash):
        return JsonResponse({'error': 'Неверный WhatsApp или пароль'}, status=400)

    request.session['seller_id'] = seller.id

    return JsonResponse({
        'status': 'ok',
        'seller_id': seller.id,
        'seller_name': seller.name,
        'must_change_password': seller.must_change_password,
    })


@csrf_exempt
def seller_logout(request):
    request.session.pop('seller_id', None)
    return JsonResponse({'status': 'ok'})


def _get_logged_seller(request):
    seller_id = request.session.get('seller_id')

    if not seller_id:
        return None

    return Seller.objects.filter(id=seller_id).first()


@csrf_exempt
def change_seller_password(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'invalid method'}, status=405)

    seller = _get_logged_seller(request)

    if not seller:
        return JsonResponse({'error': 'Требуется вход продавца'}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'invalid json'}, status=400)

    old_password = data.get('old_password') or ''
    new_password = data.get('new_password') or ''
    new_password_confirm = data.get('new_password_confirm') or ''

    if not check_password(old_password, seller.password_hash):
        return JsonResponse({'error': 'Старый пароль неверный'}, status=400)

    if len(new_password) < 6:
        return JsonResponse({'error': 'Новый пароль должен быть не короче 6 символов'}, status=400)

    if new_password != new_password_confirm:
        return JsonResponse({'error': 'Новые пароли не совпадают'}, status=400)

    seller.password_hash = make_password(new_password)
    seller.must_change_password = False
    seller.save(update_fields=['password_hash', 'must_change_password'])

    return JsonResponse({'status': 'ok'})


def seller_requests(request):
    seller = _get_logged_seller(request)

    if not seller:
        return JsonResponse({'error': 'Требуется вход продавца'}, status=401)

    period = request.GET.get('period', 'all')
    now = timezone.now()

    matches = Match.objects.filter(
        seller=seller
    ).select_related('request')

    if period == 'today':
        matches = matches.filter(request__created_at__date=now.date())

    elif period == '7d':
        matches = matches.filter(request__created_at__gte=now - timedelta(days=7))

    elif period == '30d':
        matches = matches.filter(request__created_at__gte=now - timedelta(days=30))

    data = []

    for match in matches:
        req = match.request

        status_map = {
            'prepared': 'Новая',
            'viewed': 'Просмотрена',
            'sent': 'Отправлена',
            'contacted': 'В работе',
            'done': 'Закрыта',
        }

        data.append({
            'id': req.id,
            'brand': req.brand,
            'model': req.model,
            'category': req.category,
            'city': req.city,
            'description': req.description,
            'phone': req.phone,
            'created_at': req.created_at.strftime('%d.%m.%Y %H:%M') if hasattr(req, 'created_at') else '',
            'match_id': match.id,
            'match_status': status_map.get(match.status, match.status),
        })

    return JsonResponse({
        'count': len(data),
        'requests': data,
    })


def seller_profile(request):
    seller = _get_logged_seller(request)

    if not seller:
        return JsonResponse({'error': 'Требуется вход продавца'}, status=401)

    return JsonResponse({
        'id': seller.id,
        'name': seller.name,
        'whatsapp': seller.whatsapp,
        'phone2': seller.phone2,
        'city': seller.city,
        'market_location': seller.market_location,
        'transport_type': seller.transport_type,
        'seller_type': seller.seller_type,
        'is_active': seller.is_active,
        'is_paused': seller.is_paused,
        'receive_requests': seller.receive_requests,
        'is_test_seller': seller.is_test_seller,
        'must_change_password': seller.must_change_password,

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
def toggle_seller_pause(request):
    seller = _get_logged_seller(request)

    if not seller:
        return JsonResponse({'error': 'Требуется вход продавца'}, status=401)

    seller.is_paused = not seller.is_paused
    seller.save(update_fields=['is_paused'])

    return JsonResponse({
        'status': 'ok',
        'is_paused': seller.is_paused,
    })


@csrf_exempt
def update_seller_profile(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'invalid method'}, status=405)

    seller = _get_logged_seller(request)

    if not seller:
        return JsonResponse({'error': 'Требуется вход продавца'}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'invalid json'}, status=400)

    # Основные данные магазина
    seller.name = data.get('name', seller.name)
    seller.whatsapp = _normalize_whatsapp(data.get('whatsapp', seller.whatsapp))
    seller.phone2 = data.get('phone2', seller.phone2)
    seller.city = data.get('city', seller.city)
    seller.market_location = data.get('market_location', seller.market_location)
    seller.transport_type = data.get('transport_type', seller.transport_type)

    # Статусы продавца
    seller.receive_requests = data.get('receive_requests', seller.receive_requests)
    seller.is_paused = data.get('is_paused', seller.is_paused)
    seller.is_test_seller = data.get('is_test_seller', seller.is_test_seller)

    # Флаги выбора
    seller.all_categories = data.get('all_categories', seller.all_categories)
    seller.all_countries = data.get('all_countries', seller.all_countries)
    seller.all_brands = data.get('all_brands', seller.all_brands)
    seller.all_models = data.get('all_models', seller.all_models)

    seller.save()

    # Настройки категорий / стран / марок / моделей
    if seller.all_categories:
        seller.selected_categories.clear()
    else:
        seller.selected_categories.set(data.get('selected_category_ids', []))

    if seller.all_countries:
        seller.selected_countries.clear()
    else:
        seller.selected_countries.set(data.get('selected_country_ids', []))

    if seller.all_brands:
        seller.selected_brands.clear()
    else:
        seller.selected_brands.set(data.get('selected_brand_ids', []))

    if seller.all_models:
        seller.selected_models.clear()
    else:
        seller.selected_models.set(data.get('selected_model_ids', []))

    return JsonResponse({'status': 'ok'})

@csrf_exempt
def update_match_status(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'invalid method'}, status=405)

    seller = _get_logged_seller(request)

    if not seller:
        return JsonResponse({'error': 'Требуется вход продавца'}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'invalid json'}, status=400)

    match_id = data.get('match_id')
    status = data.get('status')

    try:
        match = Match.objects.get(id=match_id, seller=seller)
    except Match.DoesNotExist:
        return JsonResponse({'error': 'Заявка не найдена'}, status=404)

    match.status = status
    match.save(update_fields=['status'])

    return JsonResponse({'status': 'ok'})

def parts_sellers_catalog(request):
    q = request.GET.get('q', '').strip()
    transport_type = request.GET.get('transport_type', '').strip()
    city = request.GET.get('city', '').strip()
    category_id = request.GET.get('category', '').strip()
    country_id = request.GET.get('country', '').strip()
    brand_id = request.GET.get('brand', '').strip()
    model_id = request.GET.get('model', '').strip()
    page = request.GET.get('page', 1)

    sellers = Seller.objects.filter(
        is_active=True,
        is_paused=False,
    ).prefetch_related(
        'selected_categories',
        'selected_countries',
        'selected_brands',
        'selected_models',
    )

    if transport_type:
        sellers = sellers.filter(
            transport_type=transport_type
        )

    if city:
        sellers = sellers.filter(
            city=city
        )

    if category_id:
        sellers = sellers.filter(
            Q(all_categories=True) |
            Q(selected_categories__id=category_id)
        ).distinct()

    if country_id:
        sellers = sellers.filter(
            Q(all_countries=True) |
            Q(selected_countries__id=country_id) |
            Q(country_fk_id=country_id)
        ).distinct()

    if brand_id:
        sellers = sellers.filter(
            Q(all_brands=True) |
            Q(selected_brands__id=brand_id) |
            Q(brand_fk_id=brand_id)
        ).distinct()

    if model_id:
        sellers = sellers.filter(
            Q(all_models=True) |
            Q(selected_models__id=model_id) |
            Q(model_fk_id=model_id)
        ).distinct()

    if q:
        sellers = sellers.filter(
            Q(name__icontains=q) |
            Q(city__icontains=q) |
            Q(market_location__icontains=q) |
            Q(notes__icontains=q)
        ).distinct()

    paginator = Paginator(
        sellers,
        20
    )

    sellers_page = paginator.get_page(
        page
    )

    cities = (
        Seller.objects.filter(
            is_active=True,
            is_paused=False,
            
        )
        .exclude(city='')
        .values_list(
            'city',
            flat=True
        )
        .distinct()
        .order_by('city')
    )

    return render(
        request,
        'catalog/parts_sellers/index.html',
        {
            'sellers': sellers_page,
            'page_obj': sellers_page,
            'filters': {
                'q': q,
                'transport_type': transport_type,
                'city': city,
                'category': category_id,
                'country': country_id,
                'brand': brand_id,
                'model': model_id,
            },
            'cities': cities,
            'categories': PartCategory.objects.all(),
            'countries': Country.objects.all(),
            'brands': Brand.objects.all(),
            'models': CarModel.objects.all(),
        }
    )


def parts_seller_detail(request, seller_id):
    seller = get_object_or_404(
        Seller.objects.prefetch_related(
            'selected_categories',
            'selected_countries',
            'selected_brands',
            'selected_models',
        ),
        id=seller_id,
        is_active=True,
        is_paused=False,
    )

    return render(
        request,
        'catalog/parts_sellers/detail.html',
        {
            'seller': seller,
        }
    )