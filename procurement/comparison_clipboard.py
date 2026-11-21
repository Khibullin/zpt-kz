"""Pilot: save Kaspi screenshots pasted directly from clipboard for one EEP comparison.

Snapshots are source evidence, not verified product attributes or pricing.
"""
import base64
import io
from PIL import Image, UnidentifiedImageError

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST
from django.db import transaction
from .models import Offer, PermanentBuyingItem, BuyingComparisonMaterial
from .views import access

MAX_IMAGE_BYTES = 500_000
MAX_SHOTS = 3


@access('sales', 'admin')
@require_POST
def paste_kaspi_screenshot(request, pk):
    offer = get_object_or_404(Offer, pk=pk, brand__iexact='EEP',
                              article__iexact='15D130002',
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
