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
    '': 'Не подтверждено',
}

CONSENT_SOURCE_LABELS = {
    'request_form': 'Форма заявки',
    'registration': 'Регистрация',
    'buyer_portal': 'Кабинет покупателя',
    'whatsapp': 'Сообщение в WhatsApp',
    'admin': 'Администратор',
    'import': 'Импорт',
    'seller_portal': 'Кабинет продавца',
    'seller_req_page': 'Страница заявки продавца',
}

MATCH_STATUS_LABELS = {
    'prepared': 'Подготовлено',
    'sent': 'Отправлено',
    'failed': 'Ошибка',
    'paused': 'Остановлено',
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

WHATSAPP_MESSAGE_TYPE_LABELS = {
    'seller_request': 'Уведомление о заявке',
    'buyer_notice': 'Уведомление клиенту',
    'manual': 'Ручная отправка',
}

DATETIME_FORMAT = 'd.m.Y H:i'
DATETIME_FORMAT_SECONDS = 'd.m.Y H:i:s'

WHATSAPP_LOG_STATUS_LABELS = {
    'sent': 'Отправлено',
    'failed': 'Ошибка отправки',
}

SELLER_SORT_CHOICES = (
    ('name', 'По имени'),
    ('activity', 'По последней активности'),
    ('sent', 'По отправленным заявкам'),
    ('opened', 'По открытиям'),
    ('whatsapp', 'По переходам в WhatsApp'),
)
SELLER_SORT_DEFAULT = 'name'

STO_SORT_CHOICES = (
    ('name', 'По названию'),
    ('city', 'По городу'),
    ('activity', 'По последней активности'),
)
STO_SORT_DEFAULT = 'name'

SERVICE_PREVIEW_LIMIT = 3


def mask_phone(value: object) -> str:
    digits = ''.join(ch for ch in str(value or '') if ch.isdigit())
    if len(digits) < 6:
        return '—'
    return f'{digits[:4]}•••{digits[-2:]}'


def request_status_label(status: str) -> str:
    value = str(status or '').strip()
    return REQUEST_STATUS_LABELS.get(value, 'Нет данных' if value else '—')


def event_label(event_type: str) -> str:
    value = str(event_type or '').strip()
    return EVENT_LABELS.get(value, 'Другое действие')


def marketing_consent_label(status: str | None) -> str:
    raw = consent_status_label(status or '')
    if raw == 'granted':
        return CONSENT_LABELS['granted']
    if raw == 'revoked':
        return CONSENT_LABELS['revoked']
    if raw == 'unknown':
        return CONSENT_LABELS['unknown']
    return CONSENT_LABELS['']


def consent_source_label(source: str | None) -> str:
    value = str(source or '').strip()
    if not value:
        return 'Не указан'
    return CONSENT_SOURCE_LABELS.get(value, 'Другой источник')


def match_status_label(status: str | None) -> str:
    value = str(status or '').strip()
    if not value:
        return '—'
    return MATCH_STATUS_LABELS.get(value, 'Нет данных')


def whatsapp_message_type_label(message_type: str | None) -> str:
    value = str(message_type or '').strip()
    if not value:
        return '—'
    return WHATSAPP_MESSAGE_TYPE_LABELS.get(value, 'Сообщение')


def reaction_consent_label(event_types: set[str], stored_status: str | None) -> str:
    if SELLER_REQUEST_PAGE_EVENT_CONSENT_YES in event_types:
        return 'Разрешил здесь'
    if SELLER_REQUEST_PAGE_EVENT_CONSENT_NO in event_types:
        return 'Отказался здесь'
    raw = consent_status_label(stored_status or '')
    if raw == 'granted':
        return 'Разрешено ранее'
    if raw == 'revoked':
        return 'Отозвано ранее'
    return 'Не выбрано'


def consent_tone(status: str | None) -> str:
    raw = consent_status_label(status or '')
    if raw == 'granted':
        return 'on'
    if raw == 'revoked':
        return 'off'
    return 'neutral'


def vehicle_label(brand: str = '', model: str = '') -> str:
    parts = [str(brand or '').strip(), str(model or '').strip()]
    text = ' '.join(part for part in parts if part)
    return text or '—'


def dash(value: object) -> str:
    text = str(value or '').strip()
    return text or '—'


def categories_text(value: object) -> str:
    text = str(value or '').strip()
    return text or 'Не указаны'


def whatsapp_log_status_label(status: str | None) -> str:
    value = str(status or '').strip()
    return WHATSAPP_LOG_STATUS_LABELS.get(value, 'Нет данных')


def _normalize_sort(value: object, choices: tuple[tuple[str, str], ...], default: str) -> str:
    raw = str(value or '').strip()
    allowed = {key for key, _label in choices}
    if raw in allowed:
        return raw
    return default


def normalize_seller_sort(value: object) -> str:
    return _normalize_sort(value, SELLER_SORT_CHOICES, SELLER_SORT_DEFAULT)


def normalize_sto_sort(value: object) -> str:
    return _normalize_sort(value, STO_SORT_CHOICES, STO_SORT_DEFAULT)


def summarize_names(names, *, limit: int = SERVICE_PREVIEW_LIMIT) -> str:
    clean = [str(item).strip() for item in names if str(item or '').strip()]
    if not clean:
        return '—'
    if len(clean) <= limit:
        return ', '.join(clean)
    return f"{', '.join(clean[:limit])} ещё {len(clean) - limit}"


def request_result_label(event_types: set[str]) -> str:
    if SELLER_REQUEST_PAGE_EVENT_OUT_OF_STOCK in event_types:
        return 'Нет в наличии'
    if SELLER_REQUEST_PAGE_EVENT_CANNOT_FULFILL in event_types:
        return 'Не могу выполнить'
    return 'Ответа нет'
