"""Owner-operated WhatsApp sends, isolated from the Meta API queue."""
from __future__ import annotations

from urllib.parse import quote

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import (Match, RequestDispatch, Seller,
    CONTACT_CONSENT_CHANNEL_WHATSAPP, CONTACT_CONSENT_PURPOSE_BUYER_REQUESTS,
    CONTACT_CONSENT_PURPOSE_MARKETING, SELLER_REGISTRATION_WHATSAPP_CONSENT_VERSION,
    SELLER_PORTAL_WHATSAPP_CONSENT_VERSION, SELLER_LINK_WHATSAPP_CONSENT_VERSION,
    SELLER_REQUEST_PAGE_WHATSAPP_CONSENT_VERSION, SELLER_PLATFORM_CONFIRM_TEMPLATE)
from core.phone_utils import normalize_kz_phone
from core.phone_utils import seller_whatsapp_phone
from core.services.seller_request_access import (
    build_seller_request_access_url,
    get_or_create_active_seller_request_access,
)
from core.services.seller_request_filters import matches_request
from core.services.test_receivers import test_receiver_decision

MIN_EVIDENCE_LENGTH = 12


LEGACY_REQUEST_CONSENT_VERSIONS = (
    SELLER_REGISTRATION_WHATSAPP_CONSENT_VERSION, SELLER_PORTAL_WHATSAPP_CONSENT_VERSION,
    SELLER_LINK_WHATSAPP_CONSENT_VERSION, SELLER_REQUEST_PAGE_WHATSAPP_CONSENT_VERSION,
    SELLER_PLATFORM_CONFIRM_TEMPLATE,
)


def manual_consent_info(seller):
    """Read existing request permissions without inventing a new consent record.

    Listed historical texts explicitly included buyer requests. Unrelated advertising
    consent is never used. Explicit request choice (including opt-out) has priority.
    """
    phone = normalize_kz_phone(seller.whatsapp)
    records = [c for c in seller.contact_consents.all() if phone and c.phone_normalized == phone
               and c.channel == CONTACT_CONSENT_CHANNEL_WHATSAPP]
    request_records = [c for c in records if c.purpose == CONTACT_CONSENT_PURPOSE_BUYER_REQUESTS]
    consent = max(request_records, key=lambda c: (c.updated_at, c.pk), default=None)
    if consent:
        return consent.status, 'buyer_requests:consent:' + str(consent.pk)
    legacy = [c for c in records if c.purpose == CONTACT_CONSENT_PURPOSE_MARKETING
              and c.consent_text_version in LEGACY_REQUEST_CONSENT_VERSIONS]
    consent = max(legacy, key=lambda c: (c.updated_at, c.pk), default=None)
    if consent:
        return consent.status, 'buyer_requests:legacy_consent:' + str(consent.pk)
    channel = getattr(seller, 'channel_connection', None)
    if channel and channel.whatsapp_consent:
        return 'granted', 'buyer_requests:whatsapp_channel:seller:' + str(seller.pk)
    return '', ''


def manual_consent_status(seller):
    return manual_consent_info(seller)[0]


def _legacy_selected(seller, req) -> bool:
    """Keep historical brand/model/category restrictions for non-JSON profiles."""
    if seller.request_filters is not None:
        return matches_request(seller.request_filters, req)
    if not seller.all_brands:
        brands = {b.name for b in seller.selected_brands.all()}
        if seller.brand:
            brands.add(seller.brand)
        if seller.brand_fk_id:
            brands.add(seller.brand_fk.name)
        if not brands or req.brand.casefold() not in {b.casefold() for b in brands}:
            return False
        if not seller.all_models:
            models = {m.name for m in seller.selected_models.all()}
            if seller.model:
                models.add(seller.model)
            if seller.model_fk_id:
                models.add(seller.model_fk.name)
            if models and req.model.casefold() not in {m.casefold() for m in models}:
                return False
    if not seller.all_categories:
        categories = {c.name for c in seller.selected_categories.all()}
        if seller.category:
            categories.add(seller.category)
        if not categories or req.category.casefold() not in {c.casefold() for c in categories}:
            return False
    return True


def outside_request_territory(req, seller):
    if test_receiver_decision(seller, req) is True:
        return False
    if req.search_scope == "city":
        return req.city != seller.city
    if req.search_scope == "custom":
        return seller.city not in {c.strip() for c in (req.selected_cities or "").split(",") if c.strip()}
    return False


def validate_manual_recipient(req, seller, *, assignment_reason="", allow_outside_territory=False):
    if not seller.is_active or seller.is_paused or seller.catalog_only:
        raise ValidationError('Продавец не подключён к получению заявок.')
    if not seller_whatsapp_phone(seller):
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
    if not allow_outside_territory and req.search_scope == 'city' and req.city != seller.city:
        raise ValidationError('Продавец вне выбранного покупателем города.')
    if not allow_outside_territory and req.search_scope == 'custom':
        cities = {c.strip() for c in (req.selected_cities or '').split(',') if c.strip()}
        if seller.city not in cities:
            raise ValidationError('Продавец вне выбранных покупателем городов.')
    if not assignment_reason and not _legacy_selected(seller, req):
        raise ValidationError('Заявка не соответствует фильтрам продавца.')


@transaction.atomic
def prepare_manual_dispatch(req, seller, *, permission_evidence='', assignment_reason='', allow_outside_territory=False):
    """Create a per-seller link only after one-off permission or prior opt-in.

    No Match row, sent timestamp, Meta call or queued wave is created yet.
    """
    assignment_reason = str(assignment_reason or '').strip()
    if assignment_reason and len(assignment_reason) < MIN_EVIDENCE_LENGTH:
        raise ValidationError('Укажите подробную причину разового назначения (от 12 символов).')
    validate_manual_recipient(req, seller, assignment_reason=assignment_reason, allow_outside_territory=allow_outside_territory)
    consent_status, evidence = manual_consent_info(seller)
    if consent_status != 'granted':
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
    phone = seller_whatsapp_phone(seller)
    url = build_seller_request_access_url(access)
    if not phone:
        return ''
    return 'https://wa.me/' + phone + '?text=' + quote(manual_whatsapp_message(req, url), safe='')
