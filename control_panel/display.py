from __future__ import annotations

from core.buyer_portal import REQUEST_STATUS_LABELS
from core.models import (
    SELLER_REQUEST_PAGE_EVENT_CALL_CLICK,
    SELLER_REQUEST_PAGE_EVENT_CANNOT_FULFILL,
    SELLER_REQUEST_PAGE_EVENT_CONSENT_NO,
    SELLER_REQUEST_PAGE_EVENT_CONSENT_YES,
    SELLER_REQUEST_PAGE_EVENT_OUT_OF_STOCK,
    SELLER_REQUEST_PAGE_EVENT_PAGE_OPEN,
    SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK,
)
from core.services.seller_whatsapp_consent import consent_status_label

EVENT_LABELS = {
    SELLER_REQUEST_PAGE_EVENT_PAGE_OPEN: 'Открыл заявку',
    SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK: 'Перешёл в WhatsApp',
    SELLER_REQUEST_PAGE_EVENT_CALL_CLICK: 'Позвонил',
    SELLER_REQUEST_PAGE_EVENT_CONSENT_YES: 'Согласился получать предложения',
    SELLER_REQUEST_PAGE_EVENT_CONSENT_NO: 'Выбрал только заявки',
    SELLER_REQUEST_PAGE_EVENT_OUT_OF_STOCK: 'Нет в наличии',
    SELLER_REQUEST_PAGE_EVENT_CANNOT_FULFILL: 'Не могу выполнить заявку',
}

CONSENT_LABELS = {
    'granted': 'Разрешено',
    'revoked': 'Отозвано',
    'unknown': 'Не подтверждено',
    '': 'Отсутствует',
}

SERVICE_MATCH_STATUS_LABELS = {
    'new': 'Новая',
    'sent': 'Отправлена',
    'viewed': 'Просмотрена',
    'in_work': 'В работе',
    'done': 'Завершена',
}

SERVICE_TYPE_LABELS = {
    'sto': 'СТО',
    'detailing': 'Детейлинг',
}

DATETIME_FORMAT = 'd.m.Y H:i'

WHATSAPP_LOG_STATUS_LABELS = {
    'sent': 'Отправлено',
    'failed': 'Ошибка отправки',
}


def mask_phone(value: object) -> str:
    digits = ''.join(ch for ch in str(value or '') if ch.isdigit())
    if len(digits) < 6:
        return '—'
    return f'{digits[:4]}•••{digits[-2:]}'


def request_status_label(status: str) -> str:
    value = str(status or '').strip()
    return REQUEST_STATUS_LABELS.get(value, value or '—')


def event_label(event_type: str) -> str:
    value = str(event_type or '').strip()
    return EVENT_LABELS.get(value, value or '—')


def marketing_consent_label(status: str | None) -> str:
    raw = consent_status_label(status or '')
    if raw == 'granted':
        return CONSENT_LABELS['granted']
    if raw == 'revoked':
        return CONSENT_LABELS['revoked']
    if raw == 'unknown':
        return CONSENT_LABELS['unknown']
    return CONSENT_LABELS['']


def vehicle_label(brand: str = '', model: str = '') -> str:
    parts = [str(brand or '').strip(), str(model or '').strip()]
    text = ' '.join(part for part in parts if part)
    return text or '—'


def dash(value: object) -> str:
    text = str(value or '').strip()
    return text or '—'


def whatsapp_log_status_label(status: str | None) -> str:
    value = str(status or '').strip()
    return WHATSAPP_LOG_STATUS_LABELS.get(value, 'Нет данных')
