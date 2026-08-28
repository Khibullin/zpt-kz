from __future__ import annotations

import os

WHATSAPP_GRAPH_API_VERSION_DEFAULT = 'v20.0'

WHATSAPP_CONFIG_INVALID_CODE = 'whatsapp_config_invalid'
WHATSAPP_CONFIG_INVALID_MESSAGE = 'WhatsApp sender configuration is invalid.'

WHATSAPP_TEMPLATE_CONFIG_INVALID_CODE = 'whatsapp_template_config_invalid'
WHATSAPP_TEMPLATE_CONFIG_INVALID_MESSAGE = (
    'WhatsApp template management configuration is invalid.'
)
WHATSAPP_WABA_ID_MISSING_MESSAGE = (
    'Для управления WhatsApp-шаблонами не настроен WHATSAPP_BUSINESS_ACCOUNT_ID.'
)


class WhatsAppSenderConfigError(Exception):
    def __init__(self) -> None:
        super().__init__(WHATSAPP_CONFIG_INVALID_MESSAGE)


class WhatsAppTemplateManagementConfigError(Exception):
    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or WHATSAPP_TEMPLATE_CONFIG_INVALID_MESSAGE)


class WhatsAppWabaIdMissingError(WhatsAppTemplateManagementConfigError):
    def __init__(self) -> None:
        super().__init__(WHATSAPP_WABA_ID_MISSING_MESSAGE)


def get_whatsapp_graph_api_version() -> str:
    env_version = (os.getenv('META_GRAPH_API_VERSION') or '').strip()
    if env_version:
        return env_version
    try:
        from django.conf import settings

        if getattr(settings, 'configured', False):
            version = (getattr(settings, 'META_GRAPH_API_VERSION', '') or '').strip()
            if version:
                return version
    except Exception:
        pass
    return WHATSAPP_GRAPH_API_VERSION_DEFAULT


def get_whatsapp_access_token() -> str:
    return (os.getenv('WHATSAPP_ACCESS_TOKEN') or '').strip()


def get_whatsapp_business_account_id() -> str:
    return (os.getenv('WHATSAPP_BUSINESS_ACCOUNT_ID') or '').strip()


def validate_whatsapp_sender_config() -> tuple[str, str]:
    phone_number_id = (os.getenv('WHATSAPP_PHONE_NUMBER_ID') or '').strip()
    access_token = get_whatsapp_access_token()

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


def validate_whatsapp_template_management_config() -> tuple[str, str]:
    waba_id = get_whatsapp_business_account_id()
    access_token = get_whatsapp_access_token()

    if not waba_id:
        raise WhatsAppWabaIdMissingError()
    if not access_token:
        raise WhatsAppTemplateManagementConfigError()
    if not waba_id.isdigit():
        raise WhatsAppTemplateManagementConfigError()
    if waba_id == access_token:
        raise WhatsAppTemplateManagementConfigError()

    return waba_id, access_token


def is_whatsapp_template_management_config_valid() -> bool:
    try:
        validate_whatsapp_template_management_config()
        return True
    except WhatsAppTemplateManagementConfigError:
        return False
