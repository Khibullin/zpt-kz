import json
import os
import re
import urllib.error
import urllib.request
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
    WhatsAppMessageLog,
)

WAVE_SIZE = 10
WAVE_INTERVAL_MINUTES = 5
TEMP_SELLER_PASSWORD = 'zpt2026'


def _normalize_whatsapp(phone):
    return ''.join(ch for ch in str(phone or '') if ch.isdigit())


# ✅ ГЛАВНАЯ ФИКС-ФУНКЦИЯ
def _wa_template_param(value):
    text = str(value or '-')

    # Убираем переносы, табы
    text = text.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')

    # Убираем множественные пробелы
    text = re.sub(r'\s+', ' ', text)

    text = text.strip()

    # Ограничение длины (на всякий случай)
    text = text[:500]

    return {
        'type': 'text',
        'text': text if text else '-',
    }


def send_whatsapp_template(to_phone, req, seller_name=''):
    phone_number_id = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
    access_token = os.getenv('WHATSAPP_ACCESS_TOKEN')
    template_name = os.getenv('WHATSAPP_TEMPLATE_NAME', 'zpt_request_notification')
    template_lang = os.getenv('WHATSAPP_TEMPLATE_LANG', 'ru')

    to_phone = _normalize_whatsapp(to_phone)

    if not phone_number_id or not access_token:
        return {'ok': False, 'error': 'WhatsApp ENV variables not set'}

    if not to_phone:
        return {'ok': False, 'error': 'Empty phone'}

    url = f'https://graph.facebook.com/v20.0/{phone_number_id}/messages'

    payload = {
        'messaging_product': 'whatsapp',
        'to': to_phone,
        'type': 'template',
        'template': {
            'name': template_name,
            'language': {'code': template_lang},
            'components': [
                {
                    'type': 'body',
                    'parameters': [
                        _wa_template_param(req.id),
                        _wa_template_param(req.brand),
                        _wa_template_param(req.model),
                        _wa_template_param(req.category),
                        _wa_template_param(req.city),
                        _wa_template_param(req.description),  # ← теперь безопасно
                        _wa_template_param(req.phone),
                    ],
                }
            ],
        },
    }

    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')

    request_obj = urllib.request.Request(
        url,
        data=body,
        method='POST',
        headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
        },
    )

    try:
        with urllib.request.urlopen(request_obj, timeout=20) as response:
            response_body = response.read().decode('utf-8')

            return {
                'ok': True,
                'status_code': response.status,
                'response': response_body,
            }

    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')

        return {
            'ok': False,
            'status_code': e.code,
            'error': error_body,
        }

    except Exception as e:
        return {
            'ok': False,
            'error': str(e),
        }


# --- ДАЛЬШЕ ТВОЙ КОД БЕЗ ИЗМЕНЕНИЙ ---


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