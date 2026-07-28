from __future__ import annotations

import os

WHATSAPP_CONFIG_INVALID_CODE = 'whatsapp_config_invalid'
WHATSAPP_CONFIG_INVALID_MESSAGE = 'WhatsApp sender configuration is invalid.'


class WhatsAppSenderConfigError(Exception):
    def __init__(self) -> None:
        super().__init__(WHATSAPP_CONFIG_INVALID_MESSAGE)


def validate_whatsapp_sender_config() -> tuple[str, str]:
    phone_number_id = (os.getenv('WHATSAPP_PHONE_NUMBER_ID') or '').strip()
    access_token = (os.getenv('WHATSAPP_ACCESS_TOKEN') or '').strip()

    if not phone_number_id or not access_token:
        raise WhatsAppSenderConfigError()
    if not phone_number_id.isdigit():
        raise WhatsAppSenderConfigError()
    if phone_number_id == access_token:
        raise WhatsAppSenderConfigError()

    return phone_number_id, access_token


def is_whatsapp_sender_config_valid() -> bool:
    try:
        validate_whatsapp_sender_config()
        return True
    except WhatsAppSenderConfigError:
        return False
