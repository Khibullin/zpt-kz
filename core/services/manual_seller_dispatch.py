"""Owner-operated WhatsApp sends, isolated from the Meta API queue."""
from __future__ import annotations

from urllib.parse import quote

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Match, RequestDispatch, Seller
from core.phone_utils import normalize_phone_for_whatsapp
from core.services.seller_buyer_request_consent import get_request_consent
from core.services.seller_request_access import (
    build_seller_request_access_url,
    get_or_create_active_seller_request_access,
)
from core.services.seller_request_filters import matches_request
from core.services.test_receivers import test_receiver_decision

MIN_EVIDENCE_LENGTH = 12


def _legacy_selected(seller, req) -> bool:
    """Keep historical brand/model/category restrictions for non-JSON profiles."""
    if seller.request_filters is not None:
        return matches_request(seller.request_filters, req)
    if not seller.all_brands:
        brands = set(seller.selected_brands.values_list('name', flat=True))
        if seller.brand:
            brands.add(seller.brand)
        if seller.brand_fk_id:
            brands.add(seller.brand_fk.name)
        if not brands or req.brand.casefold() not in {b.casefold() for b in brands}:
            return False
        if not seller.all_models:
            models = set(seller.selected_models.values_list('name', flat=True))
            if seller.model:
                models.add(seller.model)
            if seller.model_fk_id:
                models.add(seller.model_fk.name)
            if models and req.model.casefold() not in {m.casefold() for m in models}:
                return False
    if not seller.all_categories:
        categories = set(seller.selected_categories.values_list('name', flat=True))
        if seller.category:
            categories.add(seller.category)
        if not categories or req.category.casefold() not in {c.casefold() for c in categories}:
            return False
    return True


def validate_manual_recipient(req, seller, *, assignment_reason=""):
    if not seller.is_active or seller.is_paused or seller.catalog_only:
        raise ValidationError('Продавец не подключён к получению заявок.')
    if not normalize_phone_for_whatsapp(seller.whatsapp):
        raise ValidationError('У продавца нет корректного номера WhatsApp.')

    # The owner's already-authorized test recipients match *new* requests only.
    test_decision = test_receiver_decision(seller, req)
    if test_decision is True:
        return
    if test_decision is False:
        raise ValidationError('Тестовый продавец не получает исторические заявки.')

    if req.transport_type != seller.transport_type:
        raise ValidationError('Тип транспорта не совпадает.')
    if req.source == req.SOURCE_HOME_SHORT:
        if req.is_dismantler_request and not seller.is_dismantler:
            raise ValidationError('Заявка адресована только авторазборам.')
        if not req.is_dismantler_request and seller.sells_new_parts is not True:
            raise ValidationError('Продавец не подтверждён для новых запчастей.')
    if req.search_scope == 'city' and req.city != seller.city:
        raise ValidationError('Продавец вне выбранного покупателем города.')
    if req.search_scope == 'custom':
        cities = {c.strip() for c in (req.selected_cities or '').split(',') if c.strip()}
        if seller.city not in cities:
            raise ValidationError('Продавец вне выбранных покупателем городов.')
    if not assignment_reason and not _legacy_selected(seller, req):
        raise ValidationError('Заявка не соответствует фильтрам продавца.')


@transaction.atomic
def prepare_manual_dispatch(req, seller, *, permission_evidence='', assignment_reason=''):
    """Create a per-seller link only after one-off permission or prior opt-in.

    No Match row, sent timestamp, Meta call or queued wave is created yet.
    """
    assignment_reason = str(assignment_reason or '').strip()
    if assignment_reason and len(assignment_reason) < MIN_EVIDENCE_LENGTH:
        raise ValidationError('Укажите подробную причину разового назначения (от 12 символов).')
    validate_manual_recipient(req, seller, assignment_reason=assignment_reason)
    consent = get_request_consent(seller)
    if consent and consent.status == 'granted':
        evidence = 'buyer_requests:consent:' + str(consent.pk)
    else:
        evidence = str(permission_evidence or '').strip()
        if len(evidence) < MIN_EVIDENCE_LENGTH:
            raise ValidationError(
                'Сначала получите разрешение прислать эту заявку '
                'и укажите источник, дату и подтверждение продавца.'
            )
    existing = (
        RequestDispatch.objects.select_for_update()
        .filter(request=req, seller=seller).first()
    )
    if existing:
        if existing.delivery_channel != RequestDispatch.DELIVERY_MANUAL:
            raise ValidationError('Для этого продавца уже существует автоматическая отправка.')
        if existing.status not in (
            RequestDispatch.STATUS_PREPARED,
            RequestDispatch.STATUS_SENT,
        ):
            raise ValidationError('Ручная отправка находится в другом статусе.')
        dispatch = existing
    else:
        dispatch = RequestDispatch.objects.create(
            request=req,
            seller=seller,
            wave_number=0,
            position_number=0,
            status=RequestDispatch.STATUS_PREPARED,
            delivery_channel=RequestDispatch.DELIVERY_MANUAL,
            permission_evidence=evidence[:300],
            scheduled_at=timezone.now(),
        )
    access = get_or_create_active_seller_request_access(request=req, seller=seller)
    return dispatch, access


@transaction.atomic
def confirm_manual_dispatch_sent(dispatch):
    """Staff confirms clicking Send in WhatsApp; never claim API delivery."""
    dispatch = RequestDispatch.objects.select_for_update().select_related(
        'request', 'seller'
    ).get(pk=dispatch.pk)
    if dispatch.delivery_channel != RequestDispatch.DELIVERY_MANUAL:
        raise ValidationError('Подтверждение возможно только для ручного WhatsApp.')
    if dispatch.status == RequestDispatch.STATUS_SENT:
        return dispatch
    if dispatch.status != RequestDispatch.STATUS_PREPARED:
        raise ValidationError('Эта ручная отправка не подготовлена.')
    now = timezone.now()
    dispatch.status = RequestDispatch.STATUS_SENT
    dispatch.sent_at = now
    dispatch.save(update_fields=['status', 'sent_at'])
    match, created = Match.objects.get_or_create(
        request=dispatch.request,
        seller=dispatch.seller,
        defaults={'status': 'sent', 'sent_at': now},
    )
    if not created and match.status in ('prepared', 'sent'):
        match.status = 'sent'
        if match.sent_at is None:
            match.sent_at = now
        match.save(update_fields=['status', 'sent_at'])
    if dispatch.request.status == 'no_sellers':
        dispatch.request.status = 'sent'
        dispatch.request.save(update_fields=['status'])
    return dispatch


def manual_whatsapp_message(req, url: str) -> str:
    vehicle = ' '.join(filter(None, [req.brand, req.model]))
    lines = [
        f'ZPT.KZ — новая заявка №{req.pk}',
        '',
        f'Автомобиль: {vehicle or "—"}',
        f'Год: {req.year or "—"}',
        f'Категория: {req.category or "—"}',
        f'Город: {req.city or "—"}',
        '',
        'Комментарий покупателя:',
        req.description or '—',
        '',
        'Откройте персональную заявку для просмотра деталей и ответа:',
        url,
        '',
        'Ссылка персональная, действует 72 часа. '
        'Получение будущих заявок можно отдельно подтвердить на странице.',
    ]
    return '\n'.join(lines)


def manual_whatsapp_url(seller, req, access) -> str:
    phone = normalize_phone_for_whatsapp(seller.whatsapp)
    url = build_seller_request_access_url(access)
    return 'https://wa.me/' + phone + '?text=' + quote(manual_whatsapp_message(req, url), safe='')
