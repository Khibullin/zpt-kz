from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request


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
    phone_number_id = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
    access_token = os.getenv('WHATSAPP_ACCESS_TOKEN')
    to_phone = normalize_whatsapp_phone(to_phone)

    if not phone_number_id or not access_token:
        return {
            'ok': False,
            'status_code': None,
            'error': 'WhatsApp ENV variables are not configured',
            'message_id': '',
        }

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

    url = f'https://graph.facebook.com/v20.0/{phone_number_id}/messages'
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
            return {
                'ok': is_ok,
                'status_code': response.status,
                'response': response_json or response_body,
                'message_id': message_id,
                'error': None if is_ok else (response_json or response_body),
            }
    except urllib.error.HTTPError as error:
        error_body = error.read().decode('utf-8')
        return {
            'ok': False,
            'status_code': error.code,
            'error': error_body,
            'message_id': '',
        }
    except Exception as error:
        return {
            'ok': False,
            'status_code': None,
            'error': str(error),
            'message_id': '',
        }
