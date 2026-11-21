"""Pilot: save Kaspi screenshots pasted directly from clipboard for one EEP comparison.

Snapshots are source evidence, not verified product attributes or pricing.
"""
import base64
import io
import json
import hashlib
from PIL import Image, UnidentifiedImageError

from django.http import JsonResponse
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST
from django.db import transaction
from .models import Offer, PermanentBuyingItem, BuyingComparisonMaterial
from .comparison_first_ten import FIRST_TEN_OFFER_IDS
from .views import access
from .comparison_screenshot_ai import (recognize_screenshots, article_agrees,
                                       price_from_image, FIELDS, TEXT_LIMITS)

MAX_IMAGE_BYTES = 500_000
MAX_SHOTS = 3


@access('sales', 'admin')
@require_POST
def paste_kaspi_screenshot(request, pk):
    offer = get_object_or_404(Offer, pk=pk,
                              pk__in=FIRST_TEN_OFFER_IDS,
                              permanent_buying_item__isnull=False)
    record = get_object_or_404(PermanentBuyingItem, offer=offer)
    photo = request.FILES.get('screenshot')
    if photo is None or photo.size > 3_000_000:
        return JsonResponse({'error': 'Вставьте скриншот размером до 3 МБ.'}, status=400)
    try:
        image = Image.open(photo)
        image.verify()
        photo.seek(0)
        image = Image.open(photo)
        if image.width > 3500 or image.height > 3500 or image.width < 20 or image.height < 20:
            raise ValueError('Недопустимый размер изображения.')
        if image.width * image.height > 8_000_000:
            raise ValueError('Слишком большое изображение.')
        image = image.convert('RGB')
        image.thumbnail((1600, 1600))
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=78, optimize=True)
        data = buffer.getvalue()
        if len(data) > MAX_IMAGE_BYTES:
            return JsonResponse({'error': 'Слишком большой скриншот. Выделите меньшую область.'}, status=400)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        return JsonResponse({'error': 'Не удалось прочитать изображение: ' + str(exc)[:110]}, status=400)

    source_file = str(request.POST.get('source_file') or '').strip()[:255]
    product_id = str(request.POST.get('product_id') or '').strip()[:80]
    url = str(request.POST.get('kaspi_url') or '').strip()[:2048]
    encoded = 'data:image/jpeg;base64,' + base64.b64encode(data).decode('ascii')
    with transaction.atomic():
        material, _ = BuyingComparisonMaterial.objects.select_for_update().get_or_create(
            offer=offer, source_filename=source_file, kaspi_product_id=product_id,
            defaults={'supplier_url': offer.supplier_url or '',
                      'kaspi_url': url or record.kaspi_url or ''})
        existing = material.kaspi_data if isinstance(material.kaspi_data, dict) else {}
        shots = [s for s in existing.get('manual_screenshots', []) if isinstance(s, str)]
        # Recompression can change bytes; digest exact saved JPEG to avoid repeated paste.
        if encoded not in shots:
            if len(shots) >= MAX_SHOTS:
                return JsonResponse({'error': 'Для пробной карточки доступно до 3 скриншотов.'}, status=400)
            shots.append(encoded)
        material.kaspi_data = {**existing, 'manual_screenshots': shots}
        material.kaspi_status = 'manual_pending'
        material.kaspi_error = ''
        material.save(update_fields=['kaspi_data', 'kaspi_status', 'kaspi_error', 'checked_at'])
    return JsonResponse({'ok': True, 'count': len(shots), 'image': encoded,
                         'message': 'Скриншот сохранён. Характеристики требуют подтверждения.'})


def _target(request, pk):
    offer = get_object_or_404(Offer, pk=pk,
                              pk__in=FIRST_TEN_OFFER_IDS,
                              permanent_buying_item__isnull=False)
    source_file = str(request.POST.get('source_file') or '').strip()[:255]
    product_id = str(request.POST.get('product_id') or '').strip()[:80]
    material = get_object_or_404(BuyingComparisonMaterial,
                                 offer=offer, source_filename=source_file,
                                 kaspi_product_id=product_id)
    return offer, material


@access('sales', 'admin')
@require_POST
def recognize_kaspi_screenshots(request, pk):
    offer, material = _target(request, pk)
    details = material.kaspi_data if isinstance(material.kaspi_data, dict) else {}
    screenshots = [i for i in details.get('manual_screenshots', [])
                   if isinstance(i, str) and i.startswith('data:image/jpeg;base64,')][:3]
    try:
        result = recognize_screenshots(screenshots, article=offer.article, brand=offer.brand)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=503)
    # The manager sees the possible mismatch; no automatic confirmation.
    return JsonResponse({
        'ok': True, 'fields': result,
        'article_matches': article_agrees(result.get('article'), offer.article),
        'brand_matches': result.get('brand', '').casefold() == offer.brand.casefold(),
        'note': 'Предварительное распознавание. Проверьте все значения перед сохранением.'
    })


@access('sales', 'admin')
@require_POST
def confirm_kaspi_screenshots(request, pk):
    offer, material = _target(request, pk)
    details = material.kaspi_data if isinstance(material.kaspi_data, dict) else {}
    if not details.get('manual_screenshots'):
        return JsonResponse({'error': 'Нет сохранённых скриншотов.'}, status=400)
    try:
        payload = json.loads(request.body.decode('utf-8')) if request.content_type == 'application/json' else {
            field: request.POST.get(field, '') for field in FIELDS
        }
        if not isinstance(payload, dict):
            raise ValueError()
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return JsonResponse({'error': 'Неверный формат данных.'}, status=400)
    fields = {field: str(payload.get(field) or '').strip()[:TEXT_LIMITS[field]]
              for field in FIELDS}
    if not article_agrees(fields['article'], offer.article):
        return JsonResponse({'error': 'Артикул на скриншоте не совпадает с Omega. Сохранение заблокировано.'}, status=400)
    if fields['brand'].casefold() != offer.brand.casefold():
        return JsonResponse({'error': 'Бренд на скриншоте не совпадает с Omega. Сохранение заблокировано.'}, status=400)
    with transaction.atomic():
        material = BuyingComparisonMaterial.objects.select_for_update().get(pk=material.pk)
        existing = material.kaspi_data if isinstance(material.kaspi_data, dict) else {}
        # Keep screenshot price isolated from monetary fields used for profitability.
        material.kaspi_data = {
            **existing, **{k: v for k, v in fields.items() if k != 'screenshot_price'},
            'screenshot_price': price_from_image(fields['screenshot_price']),
            'manual_confirmed_at': timezone.now().isoformat(),
            'manual_confirmed_by': request.user.pk,
        }
        material.kaspi_status = 'manual_verified'
        material.kaspi_error = ''
        material.save(update_fields=['kaspi_data', 'kaspi_status', 'kaspi_error', 'checked_at'])
    return JsonResponse({'ok': True, 'message': 'Характеристики Kaspi сохранены, цены Apify не изменены.'})


@access('sales', 'admin')
@require_POST
def manage_kaspi_screenshot(request, pk):
    """Delete one screenshot or explicitly duplicate one; never touch price fields.

    A deliberate duplicate differs from accidental re-pasting, which is idempotent.
    """
    offer, material = _target(request, pk)
    action = str(request.POST.get('action') or '').strip()
    if action not in ('delete', 'duplicate'):
        return JsonResponse({'error': 'Неизвестное действие со скриншотом.'}, status=400)
    try:
        index = int(request.POST.get('index', ''))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Неверный номер скриншота.'}, status=400)
    with transaction.atomic():
        material = BuyingComparisonMaterial.objects.select_for_update().get(pk=material.pk)
        details = material.kaspi_data if isinstance(material.kaspi_data, dict) else {}
        shots = [s for s in details.get('manual_screenshots', [])
                 if isinstance(s, str) and s.startswith('data:image/jpeg;base64,')][:MAX_SHOTS]
        if not 0 <= index < len(shots):
            return JsonResponse({'error': 'Скриншот не найден. Обновите страницу.'}, status=404)
        if action == 'duplicate':
            if len(shots) >= MAX_SHOTS:
                return JsonResponse({'error': 'Для пробной карточки доступно до 3 скриншотов.'}, status=400)
            shots.insert(index + 1, shots[index])
        else:
            shots.pop(index)
        # Editing evidence revokes prior manual verification until re-confirmed.
        data = {**details, 'manual_screenshots': shots}
        data.pop('manual_confirmed_at', None)
        data.pop('manual_confirmed_by', None)
        material.kaspi_data = data
        material.kaspi_status = 'manual_pending' if shots else 'pending'
        material.kaspi_error = ''
        material.save(update_fields=['kaspi_data', 'kaspi_status', 'kaspi_error', 'checked_at'])
    return JsonResponse({'ok': True, 'count': len(shots),
                         'message': ('Скриншот удалён.' if action == 'delete'
                                     else 'Копия скриншота сохранена.')})
