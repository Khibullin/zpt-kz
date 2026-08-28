from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from core.whatsapp_config import (
    WHATSAPP_CONFIG_INVALID_CODE,
    WHATSAPP_CONFIG_INVALID_MESSAGE,
    WhatsAppSenderConfigError,
    get_whatsapp_graph_api_version,
    validate_whatsapp_sender_config,
)
from core.whatsapp_redaction import (
    redact_whatsapp_sensitive_data,
    sanitize_whatsapp_error_payload,
)


def normalize_whatsapp_phone(phone: object) -> str:
    return ''.join(ch for ch in str(phone or '') if ch.isdigit())


def wa_template_param(value: object) -> dict:
    text = str(value or '-')
    text = text.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()[:500]
    return {
        'type': 'text',
        'text': text if text else '-',
    }


def build_template_components(
    *,
    body_parameters: list | None = None,
    button_components: list | None = None,
    include_image_header: bool = False,
    header_image_url: str | None = None,
) -> list[dict]:
    components: list[dict] = []
    if include_image_header and header_image_url:
        components.append({
            'type': 'header',
            'parameters': [
                {
                    'type': 'image',
                    'image': {'link': header_image_url},
                },
            ],
        })
    components.append({
        'type': 'body',
        'parameters': body_parameters or [],
    })
    if button_components:
        components.extend(button_components)
    return components


def _invalid_config_result() -> dict:
    return {
        'ok': False,
        'status_code': None,
        'error_code': WHATSAPP_CONFIG_INVALID_CODE,
        'error': WHATSAPP_CONFIG_INVALID_MESSAGE,
        'message_id': '',
    }


def send_whatsapp_template_message(
    to_phone: str,
    *,
    template_name: str,
    template_language: str = 'ru',
    components: list | None = None,
    body_parameters: list | None = None,
    button_components: list | None = None,
    include_image_header: bool = False,
    header_image_url: str | None = None,
) -> dict:
    try:
        phone_number_id, access_token = validate_whatsapp_sender_config()
    except WhatsAppSenderConfigError:
        return _invalid_config_result()

    to_phone = normalize_whatsapp_phone(to_phone)

    if not to_phone:
        return {
            'ok': False,
            'status_code': None,
            'error': 'Recipient WhatsApp phone is empty',
            'message_id': '',
        }

    if components is None:
        components = build_template_components(
            body_parameters=body_parameters,
            button_components=button_components,
            include_image_header=include_image_header,
            header_image_url=header_image_url,
        )

    url = (
        f'https://graph.facebook.com/{get_whatsapp_graph_api_version()}'
        f'/{phone_number_id}/messages'
    )
    payload = {
        'messaging_product': 'whatsapp',
        'to': to_phone,
        'type': 'template',
        'template': {
            'name': template_name,
            'language': {'code': template_language.strip() or 'ru'},
            'components': components,
        },
    }
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    http_request = urllib.request.Request(
        url,
        data=body,
        method='POST',
        headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
        },
    )

    try:
        with urllib.request.urlopen(http_request, timeout=20) as response:
            response_body = response.read().decode('utf-8')
            try:
                response_json = json.loads(response_body)
            except Exception:
                response_json = {}
            messages = response_json.get('messages') or []
            message_id = messages[0].get('id', '') if messages else ''
            is_ok = 200 <= response.status < 300
            safe_response = redact_whatsapp_sensitive_data(response_json or response_body)
            return {
                'ok': is_ok,
                'status_code': response.status,
                'response': safe_response,
                'message_id': message_id,
                'error': None if is_ok else safe_response,
            }
    except urllib.error.HTTPError as error:
        error_body = error.read().decode('utf-8')
        return {
            'ok': False,
            'status_code': error.code,
            'error': sanitize_whatsapp_error_payload(error_body),
            'message_id': '',
        }
    except Exception as error:
        return {
            'ok': False,
            'status_code': None,
            'error': redact_whatsapp_sensitive_data(str(error)),
            'message_id': '',
        }
