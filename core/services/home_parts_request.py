"""Create a homepage short-form parts request and queue dispatch."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import (
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_SERVICE,
    CONTACT_CONSENT_SOURCE_REQUEST_FORM,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    BroadcastSettings,
    ContactConsent,
    Request,
    RequestDispatch,
)
from core.phone_utils import normalize_phone_for_whatsapp
from core.services.home_parts_query import (
    MAX_PART_POSITIONS,
    QUERY_MAX_LENGTH,
    QUERY_MIN_LENGTH,
    looks_like_exact_article,
    query_requires_vehicle,
    split_part_queries,
)
from core.services.public_rate_limit import home_parts_rate_limit_allowed
from core.services.vehicle_suggest import resolve_vehicle

logger = logging.getLogger(__name__)

DESCRIPTION_MAX_LENGTH = 2000
CITY_MAX_LENGTH = 100
BRAND_MAX_LENGTH = 100
MODEL_MAX_LENGTH = 100
VIN_MIN_LENGTH = 6
VIN_MAX_LENGTH = 17
YEAR_MIN = 1950
HOME_PARTS_CONSENT_TEXT_VERSION = 'home_parts_submit_v1'
HOME_PARTS_CONSENT_NOTICE = (
    'Нажимая кнопку, вы соглашаетесь на обработку данных '
    'и передачу запроса и телефона продавцам для подбора запчасти.'
)
ACCEPTED_MESSAGE = 'Запрос принят. Ожидайте предложения в WhatsApp.'
SAVED_WITHOUT_BROADCAST_MESSAGE = (
    'Запрос принят. Сейчас предложения в WhatsApp отправить не можем — заявка сохранена.'
)
CONFLICT_MESSAGE = 'Этот ключ уже использован для другого запроса.'
NEW_REQUEST_HINT = ' Чтобы отправить текущие данные, начните новый запрос.'


class HomePartsRequestError(Exception):
    def __init__(self, message: str, *, status: int = 400, fields: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.fields = fields or {}


@dataclass
class HomePartsPayload:
    query: str
    city: str
    phone: str
    brand_name: str = ''
    model_name: str = ''
    brand_id: str = ''
    model_id: str = ''
    year: int | None = None
    vin: str = ''
    idempotency_key: str = ''
    positions: list[str] = field(default_factory=list)
    vehicle_country: str = ''
    transport_type: str = ''
    article: str = ''


def _current_max_year() -> int:
    return timezone.now().year + 1


def _clean_text(value, *, max_length: int) -> str:
    text = ' '.join(str(value or '').split())
    if len(text) > max_length:
        raise HomePartsRequestError(
            f'Слишком длинное значение. Максимум {max_length} символов.',
        )
    return text


def parse_home_parts_data(data: dict, *, idempotency_key: str = '') -> HomePartsPayload:
    query = str(data.get('query') or data.get('description') or '').strip()
    if not query:
        raise HomePartsRequestError(
            'Напишите, какая запчасть нужна.',
            fields={'query': 'Напишите, какая запчасть нужна.'},
        )
    if len(query) < QUERY_MIN_LENGTH:
        raise HomePartsRequestError(
            'Слишком короткий запрос.',
            fields={'query': 'Слишком короткий запрос.'},
        )
    if len(query) > QUERY_MAX_LENGTH:
        raise HomePartsRequestError(
            f'Запрос слишком длинный. Максимум {QUERY_MAX_LENGTH} символов.',
            fields={'query': f'Максимум {QUERY_MAX_LENGTH} символов.'},
        )
    if len(query) > DESCRIPTION_MAX_LENGTH:
        raise HomePartsRequestError(
            'Запрос слишком длинный.',
            fields={'query': 'Запрос слишком длинный.'},
        )

    city = _clean_text(data.get('city'), max_length=CITY_MAX_LENGTH)
    if not city:
        raise HomePartsRequestError(
            'Укажите город.',
            fields={'city': 'Укажите город.'},
        )

    phone = normalize_phone_for_whatsapp(data.get('phone') or data.get('whatsapp'))
    if not phone:
        raise HomePartsRequestError(
            'Укажите корректный номер WhatsApp.',
            fields={'phone': 'Укажите корректный номер WhatsApp.'},
        )

    year = _parse_year(data.get('year'))
    vin = _parse_vin(data.get('vin'))

    positions = split_part_queries(query)
    if len(positions) > MAX_PART_POSITIONS:
        raise HomePartsRequestError(
            f'Слишком много позиций. Укажите не больше {MAX_PART_POSITIONS}.',
            fields={'query': f'Не больше {MAX_PART_POSITIONS} позиций.'},
        )

    brand_name = _clean_text(data.get('brand'), max_length=BRAND_MAX_LENGTH)
    model_name = _clean_text(data.get('model'), max_length=MODEL_MAX_LENGTH)
    if model_name in {'__custom_model__', 'Моей модели нет в списке'}:
        raise HomePartsRequestError(
            'Укажите модель автомобиля.',
            fields={'model': 'Укажите модель автомобиля.'},
        )

    vehicle = resolve_vehicle(
        brand_id=data.get('brand_id'),
        brand_name=brand_name,
        model_id=data.get('model_id'),
        model_name=model_name,
    )
    brand_name = vehicle.brand_name
    model_name = vehicle.model_name

    requires_vehicle = any(query_requires_vehicle(item) for item in positions)
    if requires_vehicle:
        if not brand_name:
            raise HomePartsRequestError(
                'Для поиска по названию укажите марку и модель автомобиля.',
                fields={'brand': 'Укажите марку автомобиля.'},
            )
        if not model_name:
            raise HomePartsRequestError(
                'Для поиска по названию укажите марку и модель автомобиля.',
                fields={'model': 'Укажите модель автомобиля.'},
            )

    article = ''
    if len(positions) == 1 and looks_like_exact_article(positions[0]):
        article = positions[0][:100]

    key = str(idempotency_key or data.get('idempotency_key') or '').strip()
    if not key or len(key) > 64 or any(ch.isspace() for ch in key):
        raise HomePartsRequestError('Некорректный ключ отправки.')

    return HomePartsPayload(
        query=query,
        city=city,
        phone=phone,
        brand_name=brand_name,
        model_name=model_name,
        brand_id=str(vehicle.brand_id or ''),
        model_id=str(vehicle.model_id or ''),
        year=year,
        vin=vin,
        idempotency_key=key,
        positions=positions,
        vehicle_country=vehicle.country,
        transport_type=vehicle.transport_type,
        article=article,
    )


def _parse_year(raw) -> int | None:
    text = str(raw or '').strip()
    if not text:
        return None
    if not text.isdigit():
        raise HomePartsRequestError(
            'Укажите год выпуска числом.',
            fields={'year': 'Укажите год выпуска числом.'},
        )
    year = int(text)
    if year < YEAR_MIN or year > _current_max_year():
        raise HomePartsRequestError(
            f'Год выпуска должен быть от {YEAR_MIN} до {_current_max_year()}.',
            fields={'year': 'Проверьте год выпуска.'},
        )
    return year


def _parse_vin(raw) -> str:
    text = str(raw or '').strip().upper().replace(' ', '')
    if not text:
        return ''
    if not text.isalnum() or len(text) < VIN_MIN_LENGTH or len(text) > VIN_MAX_LENGTH:
        raise HomePartsRequestError(
            'VIN должен содержать от 6 до 17 букв и цифр.',
            fields={'vin': 'VIN должен содержать от 6 до 17 букв и цифр.'},
        )
    return text


_BROADCAST_PROMISED_STATUSES = {
    RequestDispatch.STATUS_QUEUED,
    RequestDispatch.STATUS_SENT,
}


def broadcast_promised(*, statuses=None, queued: bool = False) -> bool:
    values = {str(item) for item in (statuses or []) if item}
    return bool(queued or values & _BROADCAST_PROMISED_STATUSES)


def buyer_confirmation_message(*, statuses=None, queued: bool = False) -> str:
    if broadcast_promised(statuses=statuses, queued=queued):
        return ACCEPTED_MESSAGE
    return SAVED_WITHOUT_BROADCAST_MESSAGE


def dispatch_status_set(dispatches) -> set[str]:
    return {item.status for item in dispatches}


def compute_idempotency_fingerprint(payload: HomePartsPayload, uploaded_photos) -> str:
    hasher = hashlib.sha256()
    parts = [
        payload.phone,
        payload.query,
        payload.city,
        payload.brand_name,
        payload.model_name,
        payload.brand_id,
        payload.model_id,
        payload.vehicle_country,
        payload.transport_type,
        str(payload.year or ''),
        payload.vin,
    ]
    hasher.update('\n'.join(parts).encode('utf-8'))
    for uploaded in uploaded_photos or []:
        name = getattr(uploaded, 'name', '') or ''
        size = getattr(uploaded, 'size', 0) or 0
        hasher.update(b'\nfile:')
        hasher.update(str(name).encode('utf-8', errors='replace'))
        hasher.update(str(size).encode('ascii'))
        try:
            position = uploaded.tell()
        except Exception:
            position = None
        try:
            uploaded.seek(0)
            hasher.update(uploaded.read())
            if position is None:
                uploaded.seek(0)
            else:
                uploaded.seek(position)
        except Exception:
            hasher.update(b'unreadable')
    return hasher.hexdigest()


def payload_matches_request(
    payload: HomePartsPayload,
    req: Request,
    uploaded_photos=None,
) -> bool:
    fingerprint = compute_idempotency_fingerprint(payload, uploaded_photos)
    stored = (req.idempotency_fingerprint or '').strip()
    if stored:
        return stored == fingerprint
    return (
        payload.phone == (req.phone or '')
        and payload.query == (req.description or '')
        and payload.city == (req.city or '')
        and payload.brand_name == (req.brand or '')
        and payload.model_name == (req.model or '')
        and payload.year == req.year
        and payload.vin == (req.vin or '')
        and payload.vehicle_country == (req.country or '')
        and payload.transport_type == (req.transport_type or '')
        and not uploaded_photos
    )


def _replay_or_conflict(
    payload: HomePartsPayload,
    existing: Request,
    uploaded_photos=None,
) -> tuple[Request, list[RequestDispatch], bool]:
    if not payload_matches_request(payload, existing, uploaded_photos):
        raise HomePartsRequestError(
            CONFLICT_MESSAGE + NEW_REQUEST_HINT,
            status=409,
        )
    dispatches = list(existing.dispatches.select_related('seller').all())
    return existing, dispatches, True


def broadcast_can_queue() -> tuple[bool, str]:
    settings = BroadcastSettings.load()
    if settings.emergency_stop:
        return False, 'emergency_stop'
    if settings.mode == BroadcastSettings.MODE_OFF:
        return False, 'mode_off'
    return True, settings.mode


def _existing_by_key(key: str) -> Request | None:
    if not key:
        return None
    return Request.objects.filter(
        source=Request.SOURCE_HOME_SHORT,
        idempotency_key=key,
    ).first()


def create_home_parts_request_record(
    payload: HomePartsPayload,
    uploaded_photos,
    *,
    replay=False,
) -> tuple[Request, list[RequestDispatch], bool]:
    from core.buyer_portal import ensure_buyer_portal_access
    from core.views import (
        _build_dispatch_queue,
        _find_matching_sellers,
        _save_request_photos,
    )

    existing = _existing_by_key(payload.idempotency_key)
    if existing is not None:
        return _replay_or_conflict(payload, existing, uploaded_photos)

    fingerprint = compute_idempotency_fingerprint(payload, uploaded_photos)
    try:
        with transaction.atomic():
            req = Request.objects.create(
                transport_type=payload.transport_type,
                country=payload.vehicle_country,
                brand=payload.brand_name,
                model=payload.model_name,
                category='',
                article=payload.article,
                description=payload.query,
                year=payload.year,
                vin=payload.vin,
                city=payload.city,
                search_scope='kazakhstan',
                selected_cities='',
                source=Request.SOURCE_HOME_SHORT,
                dispatch_mode=Request.DISPATCH_MODE_ALL_KZ,
                idempotency_key=payload.idempotency_key,
                idempotency_fingerprint=fingerprint,
                phone=payload.phone,
            )
            ensure_buyer_portal_access(req.phone)
            _save_request_photos(req, uploaded_photos)
            sellers, _strategy = _find_matching_sellers(req)
            matched = list(sellers)
            dispatches = _build_dispatch_queue(req, matched)
            req.status = 'sent' if matched else 'no_sellers'
            req.save(update_fields=['status'])
    except IntegrityError:
        existing = _existing_by_key(payload.idempotency_key)
        if existing is None:
            raise
        return _replay_or_conflict(payload, existing, uploaded_photos)

    return req, dispatches, False


def search_catalog_safe(payload: HomePartsPayload):
    try:
        from catalog.home_parts_search import groups_to_json, search_home_parts
        groups = search_home_parts(
            payload.positions,
            brand_name=payload.brand_name,
            model_name=payload.model_name,
            country=payload.vehicle_country,
            transport_type=payload.transport_type,
        )
        return groups, groups_to_json(groups), False
    except Exception:
        logger.exception('Homepage catalog search failed')
        return [], [], True


def record_home_parts_service_consent(request_id: int) -> None:
    """Record service (not marketing) consent after an explicit homepage submit."""
    req = (
        Request.objects.select_related('buyer_contact')
        .filter(pk=request_id, source=Request.SOURCE_HOME_SHORT)
        .first()
    )
    if req is None or req.buyer_contact_id is None:
        return

    now = timezone.now()
    consent, created = ContactConsent.objects.get_or_create(
        buyer=req.buyer_contact,
        channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
        purpose=CONTACT_CONSENT_PURPOSE_SERVICE,
        defaults={
            'status': CONTACT_CONSENT_STATUS_GRANTED,
            'source': CONTACT_CONSENT_SOURCE_REQUEST_FORM,
            'consent_text_version': HOME_PARTS_CONSENT_TEXT_VERSION,
            'consented_at': now,
            'evidence_reference': f'home_parts_request:{req.pk}',
        },
    )
    if created:
        return
    if consent.status != CONTACT_CONSENT_STATUS_UNKNOWN:
        return
    consent.status = CONTACT_CONSENT_STATUS_GRANTED
    consent.source = CONTACT_CONSENT_SOURCE_REQUEST_FORM
    consent.consent_text_version = HOME_PARTS_CONSENT_TEXT_VERSION
    consent.consented_at = now
    consent.evidence_reference = f'home_parts_request:{req.pk}'
    consent.save(
        update_fields=[
            'status',
            'source',
            'consent_text_version',
            'consented_at',
            'evidence_reference',
            'updated_at',
        ],
    )
