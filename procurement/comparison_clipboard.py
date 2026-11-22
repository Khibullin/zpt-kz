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
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.urls import reverse
from urllib.parse import urlencode
from django.views.decorators.http import require_POST
from django.db import transaction
from django.db.models import Q
from .models import Offer, PermanentBuyingItem, BuyingComparisonMaterial, KaspiPriceAudit, KaspiReviewTask, Activity
from .srr_review_snapshots import srr_review_snapshot
from .comparison_first_ten import FIRST_TEN_OFFER_IDS
from .comparison_next_fifty import NEXT_FIFTY_OFFER_IDS
from .comparison_next_hundred import NEXT_HUNDRED_OFFER_IDS
from .comparison_fourth_hundred import FOURTH_HUNDRED_OFFER_IDS
from .comparison_fifth_hundred import FIFTH_HUNDRED_OFFER_IDS
from .comparison_sixth_hundred import SIXTH_HUNDRED_OFFER_IDS
from .comparison_seventh_hundred import SEVENTH_HUNDRED_OFFER_IDS
from .comparison_eighth_two_hundred import EIGHTH_TWO_HUNDRED_OFFER_IDS
from .comparison_final_remaining import FINAL_REMAINING_OFFER_IDS

COMPARISON_BATCH_OFFER_IDS = (FIRST_TEN_OFFER_IDS + NEXT_FIFTY_OFFER_IDS +
                              NEXT_HUNDRED_OFFER_IDS + FOURTH_HUNDRED_OFFER_IDS +
                              FIFTH_HUNDRED_OFFER_IDS + SIXTH_HUNDRED_OFFER_IDS + SEVENTH_HUNDRED_OFFER_IDS +
                              EIGHTH_TWO_HUNDRED_OFFER_IDS + FINAL_REMAINING_OFFER_IDS)
def eligible_comparison_offer_ids():
    """Server-side queryset: only offers having a linked Kaspi card or prior review."""
    return Offer.objects.filter(
        Q(pk__in=COMPARISON_BATCH_OFFER_IDS) |
        Q(pk__in=PermanentBuyingItem.objects.values('offer_id')) |
        Q(pk__in=KaspiPriceAudit.objects.values('offer_id')) |
        Q(pk__in=BuyingComparisonMaterial.objects.values('offer_id')) |
        Q(pk__in=KaspiReviewTask.objects.filter(
            reason__startswith="Apify 21.11.2026:",
            status__in=("pending", "blocked"),
        ).values('offer_id'))
    ).values_list('pk', flat=True)


from .comparison_photo_crop import crop_photo, photo_box_from_request
from .views import access
from .comparison_screenshot_ai import (recognize_screenshots, article_agrees,
                                       price_from_image, FIELDS, TEXT_LIMITS)

MAX_IMAGE_BYTES = 500_000
MAX_SHOTS = 3

def append_screenshot_evidence(existing, encoded):
    """Add new evidence; revoke stale verification only when the set changes.

    Returning the untouched snapshot for an identical repeated paste avoids
    misleading audit timestamps and duplicate writes.
    """
    shots = [s for s in existing.get('manual_screenshots', [])
             if isinstance(s, str) and s.startswith('data:image/jpeg;base64,')]
    if encoded in shots:
        return existing, False
    if len(shots) >= MAX_SHOTS:
        raise ValueError('Для пробной карточки доступно до 3 скриншотов.')
    updated = {**existing, 'manual_screenshots': shots + [encoded]}
    updated.pop('manual_confirmed_at', None)
    updated.pop('manual_confirmed_by', None)
    if updated.get('photo_origin') == 'cropped_from_manual_screenshot':
        updated.pop('images', None)
        updated.pop('photo_origin', None)
    return updated, True



def _is_selected_card(offer, source_file, product_id):
    """Only attach screenshot evidence to the selected authoritative card.

    Older comparison materials are supported without editing historical prices.
    """
    if not product_id:
        return False
    srr = srr_review_snapshot(offer, source_file)
    if srr:
        return srr['product_id'] == product_id
    if source_file:
        return (
            KaspiPriceAudit.objects.filter(
                offer=offer, source_filename=source_file,
                product_id=product_id).exists()
            or BuyingComparisonMaterial.objects.filter(
                offer=offer, source_filename=source_file,
                kaspi_product_id=product_id).exists()
        )
    return PermanentBuyingItem.objects.filter(
        offer=offer, product_id=product_id).exists()


@access('sales', 'admin')
@require_POST
def paste_kaspi_screenshot(request, pk):
    offer = get_object_or_404(Offer, pk=pk,
                              pk__in=eligible_comparison_offer_ids())
    record = PermanentBuyingItem.objects.filter(offer=offer).first()
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
    # Unpublished items may attach evidence only to the exact second-export
    # audit or a still-unverified SRR card explicitly named in the first export.
    srr = srr_review_snapshot(offer, source_file)
    if not record and not _is_selected_card(offer, source_file, product_id):
        return JsonResponse({'error': 'Этот товар пока не допущен к сравнению.'}, status=403)
    if not _is_selected_card(offer, source_file, product_id):
        return JsonResponse({
            'error': 'Карточка Kaspi не соответствует выбранному товару и выгрузке. Обновите страницу.',
        }, status=400)
    audit = KaspiPriceAudit.objects.filter(
        offer=offer, source_filename=source_file,
        product_id=product_id).first() if source_file else None
    # Screenshot evidence must be associated with an audited URL, not a
    # user-submitted address that could belong to another card.
    existing_material = BuyingComparisonMaterial.objects.filter(
        offer=offer, source_filename=source_file,
        kaspi_product_id=product_id).first()
    url = ((audit.card_url if audit else '') or
           (existing_material.kaspi_url if existing_material else '') or
           (record.kaspi_url if record and not source_file else ''))[:2048]
    encoded = 'data:image/jpeg;base64,' + base64.b64encode(data).decode('ascii')
    with transaction.atomic():
        material, _ = BuyingComparisonMaterial.objects.select_for_update().get_or_create(
            offer=offer, source_filename=source_file, kaspi_product_id=product_id,
            defaults={'supplier_url': offer.supplier_url or '',
                      'kaspi_url': url or (record.kaspi_url if record else '')})
        existing = material.kaspi_data if isinstance(material.kaspi_data, dict) else {}
        try:
            updated, changed = append_screenshot_evidence(existing, encoded)
        except ValueError as exc:
            return JsonResponse({'error': str(exc)}, status=400)
        # Repeated paste is idempotent: it must not revoke a previous review,
        # change checked_at, or overwrite the prior manual confirmation.
        if changed:
            material.kaspi_data = updated
            material.kaspi_status = 'manual_pending'
            material.kaspi_error = ''
            material.save(update_fields=['kaspi_data', 'kaspi_status', 'kaspi_error', 'checked_at'])
    return JsonResponse({'ok': True, 'count': len(updated['manual_screenshots']), 'image': encoded,
                         'message': ('Скриншот сохранён. Характеристики требуют подтверждения.'
                                     if changed else 'Этот скриншот уже сохранён.')})


def _target(request, pk):
    offer = get_object_or_404(Offer, pk=pk,
                              pk__in=eligible_comparison_offer_ids())
    source_file = str(request.POST.get('source_file') or '').strip()[:255]
    product_id = str(request.POST.get('product_id') or '').strip()[:80]
    srr = srr_review_snapshot(offer, source_file)
    if not _is_selected_card(offer, source_file, product_id):
        from django.http import Http404
        raise Http404('Эта карточка не допущена к обработке.')
    material = get_object_or_404(BuyingComparisonMaterial,
                                 offer=offer, source_filename=source_file,
                                 kaspi_product_id=product_id)
    if not _is_selected_card(offer, source_file, product_id):
        from django.http import Http404
        raise Http404('Карточка Kaspi не соответствует выбранной выгрузке.')
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
    # Return the visual crop as a preview, not yet published product data.
    proposed_box = result.pop('product_photo_box', None)
    preview = crop_photo(screenshots, proposed_box) if proposed_box else None
    # The manager sees the possible mismatch; no automatic confirmation.
    return JsonResponse({
        'photo_box': proposed_box if preview else None,
        'photo_preview': preview or '',
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
        screenshots = [
            s for s in existing.get('manual_screenshots', [])
            if isinstance(s, str) and s.startswith('data:image/jpeg;base64,')
        ][:MAX_SHOTS]
        selected_box = photo_box_from_request(request.POST.get('photo_box', ''), len(screenshots))
        photo = crop_photo(screenshots, selected_box) if selected_box else None
        # Keep screenshot price isolated from monetary fields used for profitability.
        saved = {
            **existing, **{k: v for k, v in fields.items() if k != 'screenshot_price'},
            'screenshot_price': price_from_image(fields['screenshot_price']),
            'manual_confirmed_at': timezone.now().isoformat(),
            'manual_confirmed_by': request.user.pk,
        }
        if photo:
            previous = existing.get('images')
            old_remote = [v for v in previous if isinstance(v, str) and
                          v.startswith('https://')] if isinstance(previous, list) else []
            saved['images'] = [photo] + old_remote[:7]
            saved['photo_origin'] = 'cropped_from_manual_screenshot'
        material.kaspi_data = saved
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
        if data.get('photo_origin') == 'cropped_from_manual_screenshot':
            data.pop('images', None)
            data.pop('photo_origin', None)
        data.pop('manual_confirmed_at', None)
        data.pop('manual_confirmed_by', None)
        material.kaspi_data = data
        material.kaspi_status = 'manual_pending' if shots else 'pending'
        material.kaspi_error = ''
        material.save(update_fields=['kaspi_data', 'kaspi_status', 'kaspi_error', 'checked_at'])
    return JsonResponse({'ok': True, 'count': len(shots),
                         'message': ('Скриншот удалён.' if action == 'delete'
                                     else 'Копия скриншота сохранена.')})


@access('sales', 'admin')
@require_POST
def review_kaspi_identity(request, pk):
    """Explicit human identity decision for one audited source/card pair.

    Does not change any recorded supplier or Kaspi price, or automatically
    admit a candidate to the permanent buying register.
    """
    offer = get_object_or_404(Offer, pk=pk)
    source = str(request.POST.get('source_file') or '').strip()[:255]
    product = str(request.POST.get('product_id') or '').strip()[:80]
    audit = get_object_or_404(KaspiPriceAudit, offer=offer,
                              source_filename=source, product_id=product)
    status = str(request.POST.get('identity_status') or '').strip()
    if status not in ('verified', 'needs_review', 'mismatch'):
        return JsonResponse({'error': 'Неизвестный статус соответствия.'}, status=400)
    if status == 'verified':
        if request.POST.get('confirm_all') != 'yes':
            return JsonResponse({'error': 'Подтвердите проверку наименования, бренда, артикула, фото, характеристик, применимости и комплектации.'}, status=400)
        material = BuyingComparisonMaterial.objects.filter(
            offer=offer, source_filename=source, kaspi_product_id=product).first()
        supplier = offer.characteristics if isinstance(offer.characteristics, dict) else {}
        kaspi = material.kaspi_data if material and isinstance(material.kaspi_data, dict) else {}
        supplier_material = material.supplier_data if material and isinstance(material.supplier_data, dict) else {}
        supplier_images = offer.photo_url or supplier.get('supplier_images') or supplier_material.get('images')
        kaspi_source = supplier.get('kaspi_card_details') or supplier.get('kaspi_details') or {}
        firsthand = supplier.get('firsthand_omega_20261117') or {}
        kaspi_images = kaspi.get('images') or (
            kaspi_source.get('images') if isinstance(kaspi_source, dict) else None) or (
            firsthand.get('images') if isinstance(firsthand, dict) and
            str(firsthand.get('kaspi_product_id', '')) == product else None)
        supplier_specs = (supplier_material.get('specs') or supplier.get('technical_characteristics')
                          or supplier.get('specifications') or supplier.get('technical_specs'))
        if not (material and material.kaspi_status == 'manual_verified'
                and kaspi.get('manual_confirmed_at')):
            return JsonResponse({'error': 'Сначала сохраните проверенные сведения Kaspi по скриншотам.'}, status=400)
        if not (article_agrees(kaspi.get('article'), offer.article)
                and str(kaspi.get('brand') or '').casefold() == str(offer.brand).casefold()):
            return JsonResponse({'error': 'Бренд или полный артикул не совпадают.'}, status=400)
        if not (supplier_images and kaspi_images and supplier_specs and
                (kaspi.get('specs') or kaspi.get('characteristics')) and
                (offer.applicability or supplier_material.get('fitment')) and
                kaspi.get('fitment') and kaspi.get('name') and
                kaspi.get('package')):
            return JsonResponse({'error': 'Недостаточно фото, характеристик, применимости или комплектности. Оставьте «Требует проверки».'}, status=400)
        from .omega_policy import POSITIVE_STOCK_PATTERN
        import re
        if not (audit.roi >= 50 and audit.cost_snapshot == offer.cost
                and re.match(POSITIVE_STOCK_PATTERN, str(offer.stock or ''))
                and audit.min_price > 0 and audit.seller_name):
            return JsonResponse({'error': 'Доходность, остаток, закупочная цена или цена продавца не подтверждены.'}, status=400)
    before = audit.identity_status
    if before != status:
        audit.identity_status = status
        audit.save(update_fields=['identity_status'])
        Activity.objects.create(
            user=request.user, action='kaspi_identity_review',
            details={'offer': offer.pk, 'source': source, 'product_id': product,
                     'previous_status': before, 'status': status})
    messages.success(request, {
        'verified': 'Совпадение подтверждено. Финансовые условия перед закупкой проверяются повторно.',
        'needs_review': 'Карточка оставлена на проверку.',
        'mismatch': 'Карточка помечена «Не соответствует». Закупка заблокирована.',
    }[status])
    return redirect(reverse('procurement:comparison', args=[offer.pk]) + '?' +
                    urlencode({'apify_source': source}))
