from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone

from core.whatsapp_config import (
    WhatsAppTemplateManagementConfigError,
    WhatsAppWabaIdMissingError,
    get_whatsapp_graph_api_version,
    validate_whatsapp_template_management_config,
)
from core.whatsapp_redaction import (
    redact_whatsapp_sensitive_data,
    sanitize_persisted_whatsapp_error_message,
    sanitize_whatsapp_error_payload,
)
from marketing.services.templates.constants import (
    BUTTON_TYPE_PHONE,
    BUTTON_TYPE_QUICK_REPLY,
    BUTTON_TYPE_URL,
    META_STATUS_APPROVED,
    META_STATUS_DISABLED,
    META_STATUS_DRAFT,
    META_STATUS_PAUSED,
    META_STATUS_PENDING,
    META_STATUS_REJECTED,
    META_STATUS_UNKNOWN,
)

logger = logging.getLogger(__name__)

META_HTTP_TIMEOUT_SECONDS = 20

ALREADY_EXISTS_MESSAGE = 'Шаблон уже существует в Meta. Статус синхронизирован.'
CONTENT_MISMATCH_MESSAGE = (
    'Шаблон с таким именем уже существует в Meta, '
    'но его содержимое отличается. Требуется ручная проверка.'
)
NOT_FOUND_MESSAGE = 'Шаблон с таким именем не найден в Meta.'
APPROVED_MESSAGE = (
    'Шаблон одобрен Meta и может использоваться совместимой кампанией.'
)

_META_STATUS_MAP = {
    'PENDING': META_STATUS_PENDING,
    'APPROVED': META_STATUS_APPROVED,
    'REJECTED': META_STATUS_REJECTED,
    'PAUSED': META_STATUS_PAUSED,
    'DISABLED': META_STATUS_DISABLED,
    'DRAFT': META_STATUS_DRAFT,
    'UNKNOWN': META_STATUS_UNKNOWN,
}


@dataclass
class TemplateManagementResult:
    ok: bool
    message: str = ''
    created: bool = False
    already_exists: bool = False
    conflict: bool = False
    not_found: bool = False
    config_error: bool = False
    waba_missing: bool = False
    meta_status: str = ''
    meta_template_id: str = ''
    error: Any = None
    meta_template: dict | None = None
    http_called: list[str] = field(default_factory=list)


def map_meta_template_status(raw: object) -> str:
    text = str(raw or '').strip().upper()
    if not text:
        return META_STATUS_UNKNOWN
    return _META_STATUS_MAP.get(text, META_STATUS_UNKNOWN)


def _graph_url(waba_id: str, suffix: str = '', query: dict | None = None) -> str:
    version = get_whatsapp_graph_api_version()
    path = f'{waba_id}/message_templates'
    if suffix:
        path = f'{path}/{suffix.lstrip("/")}'
    url = f'https://graph.facebook.com/{version}/{path}'
    if query:
        url = f'{url}?{urllib.parse.urlencode(query)}'
    return url


def build_meta_template_payload(template) -> dict:
    language = str(getattr(template, 'language_code', '') or 'ru').strip() or 'ru'
    category = str(getattr(template, 'category', 'MARKETING') or 'MARKETING').strip()
    payload = {
        'name': str(getattr(template, 'meta_template_name', '') or '').strip(),
        'language': language,
        'category': category.upper() or 'MARKETING',
        'components': [],
    }
    header_text = str(getattr(template, 'header_text', '') or '').strip()
    body_text = str(getattr(template, 'body_text', '') or '')
    footer_text = str(getattr(template, 'footer_text', '') or '').strip()
    if header_text:
        payload['components'].append({
            'type': 'HEADER',
            'format': 'TEXT',
            'text': header_text,
        })
    payload['components'].append({
        'type': 'BODY',
        'text': body_text,
    })
    if footer_text:
        payload['components'].append({
            'type': 'FOOTER',
            'text': footer_text,
        })
    meta_buttons = _local_buttons_to_meta(getattr(template, 'buttons', None))
    if meta_buttons:
        payload['components'].append({
            'type': 'BUTTONS',
            'buttons': meta_buttons,
        })
    return payload


def _local_buttons_to_meta(raw_buttons) -> list[dict]:
    converted: list[dict] = []
    if not isinstance(raw_buttons, list):
        return converted
    for item in raw_buttons:
        if not isinstance(item, dict):
            continue
        button_type = str(item.get('type') or '').strip().lower()
        text = str(item.get('text') or '').strip()
        value = str(item.get('value') or item.get('url') or '').strip()
        if button_type == BUTTON_TYPE_URL and text and value:
            converted.append({
                'type': 'URL',
                'text': text,
                'url': value,
            })
        elif button_type == BUTTON_TYPE_PHONE and text and value:
            converted.append({
                'type': 'PHONE_NUMBER',
                'text': text,
                'phone_number': value,
            })
        elif button_type == BUTTON_TYPE_QUICK_REPLY and text:
            converted.append({
                'type': 'QUICK_REPLY',
                'text': text,
            })
    return converted


def _component_text(component: dict, *keys: str) -> str:
    for key in keys:
        if key in component and component.get(key) is not None:
            return str(component.get(key) or '')
    return ''


def _normalize_language(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get('code') or value.get('language') or '').strip().lower()
    return str(value or '').strip().lower()


def _normalize_buttons(buttons: list) -> list[tuple[str, str, str]]:
    normalized: list[tuple[str, str, str]] = []
    if not isinstance(buttons, list):
        return normalized
    for item in buttons:
        if not isinstance(item, dict):
            continue
        button_type = str(item.get('type') or '').strip().upper()
        if button_type == 'URL':
            button_type = 'URL'
        elif button_type in {'PHONE', 'PHONE_NUMBER'}:
            button_type = 'PHONE_NUMBER'
        elif button_type in {'QUICK_REPLY', 'QUICKREPLY'}:
            button_type = 'QUICK_REPLY'
        text = str(item.get('text') or '').strip()
        url = str(
            item.get('url')
            or item.get('value')
            or item.get('phone_number')
            or ''
        ).strip()
        normalized.append((button_type, text, url))
    return normalized


def _content_from_components(components) -> dict:
    header = ''
    body = ''
    footer = ''
    buttons: list[tuple[str, str, str]] = []
    if not isinstance(components, list):
        return {
            'header': header,
            'body': body,
            'footer': footer,
            'buttons': buttons,
        }
    for component in components:
        if not isinstance(component, dict):
            continue
        ctype = str(component.get('type') or '').strip().upper()
        if ctype == 'HEADER':
            header = _component_text(component, 'text').strip()
        elif ctype == 'BODY':
            body = _component_text(component, 'text')
        elif ctype == 'FOOTER':
            footer = _component_text(component, 'text').strip()
        elif ctype == 'BUTTONS':
            buttons = _normalize_buttons(component.get('buttons') or [])
    return {
        'header': header,
        'body': body,
        'footer': footer,
        'buttons': buttons,
    }


def local_template_content_signature(template) -> dict:
    payload = build_meta_template_payload(template)
    return _content_from_components(payload.get('components'))


def meta_template_content_signature(meta_template: dict) -> dict:
    return _content_from_components(meta_template.get('components'))


def templates_content_matches(template, meta_template: dict) -> bool:
    local = local_template_content_signature(template)
    remote = meta_template_content_signature(meta_template)
    return local == remote


def _meta_template_id(meta_template: dict) -> str:
    return str(meta_template.get('id') or meta_template.get('name') or '').strip()


def _http_json(
    *,
    method: str,
    url: str,
    access_token: str,
    payload: dict | None = None,
) -> tuple[int | None, Any, bool]:
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json',
    }
    data = None
    if payload is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=META_HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read().decode('utf-8')
            status = getattr(response, 'status', 200)
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = {'raw': raw}
            return status, parsed, True
    except urllib.error.HTTPError as error:
        error_body = error.read().decode('utf-8', errors='replace')
        safe = sanitize_whatsapp_error_payload(error_body)
        logger.warning(
            'WhatsApp template Meta HTTP error status=%s body=%s',
            error.code,
            safe,
        )
        return error.code, safe, False
    except Exception as error:
        safe = redact_whatsapp_sensitive_data(str(error))
        logger.warning('WhatsApp template Meta request failed: %s', safe)
        return None, safe, False


def get_whatsapp_template_by_name(
    template_name: str,
    *,
    language_code: str = 'ru',
) -> TemplateManagementResult:
    try:
        waba_id, access_token = validate_whatsapp_template_management_config()
    except WhatsAppWabaIdMissingError as exc:
        return TemplateManagementResult(
            ok=False,
            config_error=True,
            waba_missing=True,
            message=str(exc),
        )
    except WhatsAppTemplateManagementConfigError as exc:
        return TemplateManagementResult(
            ok=False,
            config_error=True,
            message=str(exc),
        )

    name = str(template_name or '').strip()
    url = _graph_url(
        waba_id,
        query={
            'name': name,
            'fields': 'id,name,status,language,category,components',
        },
    )
    status_code, payload, http_ok = _http_json(
        method='GET',
        url=url,
        access_token=access_token,
    )
    if not http_ok:
        return TemplateManagementResult(
            ok=False,
            message=sanitize_persisted_whatsapp_error_message(payload),
            error=payload,
            http_called=['GET'],
        )
    wanted_language = _normalize_language(language_code)
    rows = []
    if isinstance(payload, dict):
        rows = payload.get('data') or []
    if not isinstance(rows, list):
        rows = []
    match = None
    for item in rows:
        if not isinstance(item, dict):
            continue
        item_name = str(item.get('name') or '').strip()
        if item_name != name:
            continue
        item_language = _normalize_language(item.get('language'))
        if item_language == wanted_language or item_language.startswith(wanted_language):
            match = item
            break
    if match is None:
        return TemplateManagementResult(
            ok=True,
            not_found=True,
            message=NOT_FOUND_MESSAGE,
            http_called=['GET'],
        )
    return TemplateManagementResult(
        ok=True,
        meta_template_id=_meta_template_id(match),
        meta_status=map_meta_template_status(match.get('status')),
        message='ok',
        http_called=['GET'],
        meta_template=match,
    )


def _apply_sync(template, *, meta_template_id: str, meta_status: str) -> None:
    template.meta_template_id = str(meta_template_id or '')
    template.meta_status = meta_status or META_STATUS_UNKNOWN
    template.last_status_checked_at = timezone.now()
    template.save(update_fields=[
        'meta_template_id',
        'meta_status',
        'last_status_checked_at',
        'updated_at',
    ])


def sync_whatsapp_template_status(template) -> TemplateManagementResult:
    result = get_whatsapp_template_by_name(
        template.meta_template_name,
        language_code=template.language_code,
    )
    if result.config_error or not result.ok:
        return result
    if result.not_found:
        previous_status = template.meta_status
        _apply_sync(template, meta_template_id='', meta_status=META_STATUS_UNKNOWN)
        result.meta_status = META_STATUS_UNKNOWN
        result.meta_template_id = ''
        if previous_status == META_STATUS_APPROVED:
            result.message = NOT_FOUND_MESSAGE
        return result
    _apply_sync(
        template,
        meta_template_id=result.meta_template_id,
        meta_status=result.meta_status,
    )
    if result.meta_status == META_STATUS_APPROVED:
        result.message = APPROVED_MESSAGE
    else:
        result.message = f'Статус в Meta синхронизирован: {result.meta_status}.'
    return result


def submit_whatsapp_template(template) -> TemplateManagementResult:
    try:
        waba_id, access_token = validate_whatsapp_template_management_config()
    except WhatsAppWabaIdMissingError as exc:
        return TemplateManagementResult(
            ok=False,
            config_error=True,
            waba_missing=True,
            message=str(exc),
        )
    except WhatsAppTemplateManagementConfigError as exc:
        return TemplateManagementResult(
            ok=False,
            config_error=True,
            message=str(exc),
        )

    existing = get_whatsapp_template_by_name(
        template.meta_template_name,
        language_code=template.language_code,
    )
    if existing.config_error or not existing.ok:
        return existing
    if not existing.not_found:
        meta_row = existing.meta_template or {}
        if not templates_content_matches(template, meta_row):
            return TemplateManagementResult(
                ok=False,
                conflict=True,
                message=CONTENT_MISMATCH_MESSAGE,
                meta_status=existing.meta_status,
                meta_template_id=existing.meta_template_id,
                http_called=['GET'],
            )
        _apply_sync(
            template,
            meta_template_id=existing.meta_template_id,
            meta_status=existing.meta_status,
        )
        return TemplateManagementResult(
            ok=True,
            already_exists=True,
            message=ALREADY_EXISTS_MESSAGE,
            meta_status=existing.meta_status,
            meta_template_id=existing.meta_template_id,
            http_called=['GET'],
        )

    payload = build_meta_template_payload(template)
    url = _graph_url(waba_id)
    status_code, response_payload, http_ok = _http_json(
        method='POST',
        url=url,
        access_token=access_token,
        payload=payload,
    )
    http_called = ['GET', 'POST']
    if not http_ok:
        return TemplateManagementResult(
            ok=False,
            message=sanitize_persisted_whatsapp_error_message(response_payload),
            error=response_payload,
            http_called=http_called,
        )
    if not isinstance(response_payload, dict):
        return TemplateManagementResult(
            ok=False,
            message='Некорректный ответ Meta.',
            error=redact_whatsapp_sensitive_data(response_payload),
            http_called=http_called,
        )
    meta_id = str(response_payload.get('id') or '').strip()
    mapped_status = map_meta_template_status(response_payload.get('status'))
    if mapped_status == META_STATUS_APPROVED and str(
        response_payload.get('status') or ''
    ).strip().upper() != 'APPROVED':
        mapped_status = META_STATUS_UNKNOWN
    _apply_sync(template, meta_template_id=meta_id, meta_status=mapped_status)
    message = 'Шаблон отправлен в Meta на согласование.'
    if mapped_status == META_STATUS_APPROVED:
        message = APPROVED_MESSAGE
    elif mapped_status == META_STATUS_PENDING:
        message = 'Шаблон отправлен в Meta. Статус: на проверке (pending).'
    return TemplateManagementResult(
        ok=True,
        created=True,
        message=message,
        meta_status=mapped_status,
        meta_template_id=meta_id,
        http_called=http_called,
    )
