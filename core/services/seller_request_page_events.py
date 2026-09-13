from __future__ import annotations

from django.db import IntegrityError, transaction

from core.models import (
    SELLER_REQUEST_PAGE_CONSENT_TYPES,
    SELLER_REQUEST_PAGE_EVENT_CALL_CLICK,
    SELLER_REQUEST_PAGE_EVENT_CANNOT_FULFILL,
    SELLER_REQUEST_PAGE_EVENT_CONSENT_NO,
    SELLER_REQUEST_PAGE_EVENT_CONSENT_YES,
    SELLER_REQUEST_PAGE_EVENT_OUT_OF_STOCK,
    SELLER_REQUEST_PAGE_EVENT_PAGE_OPEN,
    SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK,
    SELLER_REQUEST_PAGE_OUTCOME_TYPES,
    SellerRequestAccess,
    SellerRequestPageEvent,
)

EVENT_PAGE_OPEN = SELLER_REQUEST_PAGE_EVENT_PAGE_OPEN
EVENT_WHATSAPP_CLICK = SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK
EVENT_CALL_CLICK = SELLER_REQUEST_PAGE_EVENT_CALL_CLICK
EVENT_CONSENT_YES = SELLER_REQUEST_PAGE_EVENT_CONSENT_YES
EVENT_CONSENT_NO = SELLER_REQUEST_PAGE_EVENT_CONSENT_NO
EVENT_OUT_OF_STOCK = SELLER_REQUEST_PAGE_EVENT_OUT_OF_STOCK
EVENT_CANNOT_FULFILL = SELLER_REQUEST_PAGE_EVENT_CANNOT_FULFILL

OUTCOME_MESSAGES = {
    EVENT_OUT_OF_STOCK: 'Отметили: нет в наличии.',
    EVENT_CANNOT_FULFILL: 'Отметили: не можете выполнить заявку.',
}

OUTCOME_CONFLICT_MESSAGES = {
    EVENT_OUT_OF_STOCK: 'Уже отмечено: нет в наличии.',
    EVENT_CANNOT_FULFILL: 'Уже отмечено: не можете выполнить заявку.',
}

CONSENT_CONFLICT_MESSAGES = {
    EVENT_CONSENT_YES: 'Выбор уже сохранён: получать предложения.',
    EVENT_CONSENT_NO: 'Выбор уже сохранён: только заявки.',
}

SELLER_MATCH_STATUS_LABELS = {
    'prepared': 'Новая',
    'viewed': 'Просмотрена',
    'sent': 'Отправлена',
    'contacted': 'В работе',
    'done': 'Закрыта',
    'unavailable': 'Нет в наличии',
    'declined': 'Не могу выполнить заявку',
    EVENT_OUT_OF_STOCK: 'Нет в наличии',
    EVENT_CANNOT_FULFILL: 'Не могу выполнить заявку',
}


def seller_match_status_label(status: str) -> str:
    value = str(status or '').strip()
    return SELLER_MATCH_STATUS_LABELS.get(value, value)


def can_record_page_event(access: SellerRequestAccess | None, req) -> bool:
    return bool(
        access is not None
        and access.pk
        and access.seller_id
        and req is not None
        and getattr(req, 'pk', None)
    )


def get_seller_request_consent_event(
    access: SellerRequestAccess,
) -> SellerRequestPageEvent | None:
    return (
        SellerRequestPageEvent.objects.filter(
            access=access,
            event_type__in=SELLER_REQUEST_PAGE_CONSENT_TYPES,
        )
        .order_by('created_at', 'id')
        .first()
    )


def get_seller_request_outcome(*, request_id: int, seller_id: int) -> SellerRequestPageEvent | None:
    return (
        SellerRequestPageEvent.objects.filter(
            request_id=request_id,
            seller_id=seller_id,
            event_type__in=SELLER_REQUEST_PAGE_OUTCOME_TYPES,
        )
        .order_by('created_at', 'id')
        .first()
    )


def record_page_event(
    access: SellerRequestAccess,
    event_type: str,
    *,
    request,
    seller,
) -> tuple[SellerRequestPageEvent | None, bool]:
    """Create the event once per access+type. Returns (event, created)."""
    if request is None or seller is None:
        return None, False
    try:
        with transaction.atomic():
            event = SellerRequestPageEvent.objects.create(
                access=access,
                request=request,
                seller=seller,
                event_type=event_type,
            )
        return event, True
    except IntegrityError:
        existing = (
            SellerRequestPageEvent.objects.filter(
                access=access,
                event_type=event_type,
            )
            .order_by('id')
            .first()
        )
        return existing, False


def save_seller_request_outcome(
    access: SellerRequestAccess,
    outcome: str,
    *,
    request,
    seller,
) -> tuple[SellerRequestPageEvent | None, bool, bool]:
    """Store the first outcome for request+seller.

    Returns (event, accepted, created).
    accepted is True when the stored outcome matches the requested one.
    """
    if request is None or seller is None:
        return None, False, False
    if outcome not in SELLER_REQUEST_PAGE_OUTCOME_TYPES:
        return None, False, False

    with transaction.atomic():
        locked_access = (
            SellerRequestAccess.objects.select_for_update().filter(pk=access.pk).first()
        )
        if locked_access is None:
            return None, False, False
        existing = get_seller_request_outcome(
            request_id=request.pk,
            seller_id=seller.pk,
        )
        if existing is not None:
            return existing, existing.event_type == outcome, False
        try:
            event = SellerRequestPageEvent.objects.create(
                access=locked_access,
                request=request,
                seller=seller,
                event_type=outcome,
            )
            return event, True, True
        except IntegrityError:
            existing = get_seller_request_outcome(
                request_id=request.pk,
                seller_id=seller.pk,
            )
            if existing is None:
                return None, False, False
            return existing, existing.event_type == outcome, False


def save_seller_request_consent_choice(
    access: SellerRequestAccess,
    granted: bool,
    *,
    request,
    seller,
    evidence_reference: str,
) -> tuple[SellerRequestPageEvent | None, bool, bool]:
    """Store the first marketing choice for this access without flipping later."""
    from core.services.seller_whatsapp_consent import (
        apply_seller_request_page_marketing_consent,
    )

    if request is None or seller is None:
        return None, False, False
    desired = EVENT_CONSENT_YES if granted else EVENT_CONSENT_NO
    with transaction.atomic():
        locked_access = (
            SellerRequestAccess.objects.select_for_update().filter(pk=access.pk).first()
        )
        if locked_access is None:
            return None, False, False
        existing = get_seller_request_consent_event(locked_access)
        if existing is not None:
            return existing, existing.event_type == desired, False
        apply_seller_request_page_marketing_consent(
            seller,
            granted=granted,
            evidence_reference=evidence_reference,
        )
        try:
            event = SellerRequestPageEvent.objects.create(
                access=locked_access,
                request=request,
                seller=seller,
                event_type=desired,
            )
            return event, True, True
        except IntegrityError:
            existing = get_seller_request_consent_event(locked_access)
            if existing is None:
                return None, False, False
            return existing, existing.event_type == desired, False
