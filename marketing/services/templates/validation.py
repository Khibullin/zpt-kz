from __future__ import annotations

import re
from urllib.parse import urlparse

from marketing.services.templates.constants import (
    BUTTON_TYPE_CHOICES,
    BUTTON_TYPE_PHONE,
    BUTTON_TYPE_QUICK_REPLY,
    BUTTON_TYPE_URL,
    FORBIDDEN_VARIABLE_EXTRA_FIELDS,
    FORBIDDEN_VARIABLE_KEYS,
    MAX_BUTTONS,
    MAX_BUTTON_TEXT_LENGTH,
    MAX_BUTTON_VALUE_LENGTH,
    MAX_VARIABLE_EXAMPLE_LENGTH,
    MAX_VARIABLE_KEY_LENGTH,
    MAX_VARIABLE_LABEL_LENGTH,
    MAX_VARIABLES,
    TEMPLATE_BUSINESS_PURPOSE_CODES,
    get_reserved_service_template_names,
)

VARIABLE_KEY_PATTERN = re.compile(r'^[a-z][a-z0-9_]*$')
META_TEMPLATE_NAME_PATTERN = re.compile(r'^[a-z0-9_]+$')
LANGUAGE_CODE_PATTERN = re.compile(r'^[a-z]{2}(_[A-Z]{2})?$')
DANGEROUS_URL_SCHEME_PATTERN = re.compile(r'^(javascript|data):', re.IGNORECASE)

BUTTON_TYPES = frozenset(value for value, _ in BUTTON_TYPE_CHOICES)


class TemplateValidationError(Exception):
    pass


def is_reserved_service_template_name(value: str) -> bool:
    normalized = (value or '').strip().lower()
    return normalized in get_reserved_service_template_names()


def validate_meta_template_name(value: str) -> str:
    cleaned = (value or '').strip().lower()
    if not cleaned:
        raise TemplateValidationError('Укажите Meta template name.')
    if len(cleaned) > 150:
        raise TemplateValidationError('Meta template name слишком длинный.')
    if not META_TEMPLATE_NAME_PATTERN.fullmatch(cleaned):
        raise TemplateValidationError(
            'Meta template name может содержать только строчные latinские буквы, цифры и подчёркивание.',
        )
    if is_reserved_service_template_name(cleaned):
        raise TemplateValidationError(
            'Это имя зарезервировано для сервисных WhatsApp-шаблонов и не может использоваться в маркетинге.',
        )
    return cleaned


def validate_language_code(value: str) -> str:
    cleaned = (value or '').strip()
    if not cleaned:
        raise TemplateValidationError('Укажите код языка шаблона.')
    if len(cleaned) > 20:
        raise TemplateValidationError('Код языка слишком длинный.')
    if not LANGUAGE_CODE_PATTERN.fullmatch(cleaned):
        raise TemplateValidationError('Код языка должен быть в формате ru или ru_RU.')
    return cleaned


def validate_allowed_purposes(values: list[str]) -> list[str]:
    if not isinstance(values, list):
        raise TemplateValidationError('Разрешённые назначения должны быть списком.')
    normalized: list[str] = []
    for value in values:
        code = (value or '').strip()
        if not code:
            continue
        if code not in TEMPLATE_BUSINESS_PURPOSE_CODES:
            raise TemplateValidationError(f'Недопустимое назначение шаблона: {code}.')
        if code not in normalized:
            normalized.append(code)
    return normalized


def is_empty_variable_placeholder(item: dict) -> bool:
    key = str(item.get('key', '')).strip()
    label = str(item.get('label', '')).strip()
    example = str(item.get('example', '')).strip()
    required = bool(item.get('required', False))
    return not key and not label and not example and not required


def is_empty_button_placeholder(item: dict) -> bool:
    text = str(item.get('text', '')).strip()
    value = str(item.get('value', '')).strip()
    return not text and not value


def validate_variable_key(key: str) -> str:
    cleaned = (key or '').strip()
    if not cleaned:
        raise TemplateValidationError('Укажите key переменной.')
    if len(cleaned) > MAX_VARIABLE_KEY_LENGTH:
        raise TemplateValidationError('Key переменной слишком длинный.')
    if not VARIABLE_KEY_PATTERN.fullmatch(cleaned):
        raise TemplateValidationError(
            'Key переменной должен начинаться с буквы и содержать только a-z, 0-9 и _.',
        )
    if cleaned in FORBIDDEN_VARIABLE_KEYS:
        raise TemplateValidationError(f'Key «{cleaned}» запрещён в variables.')
    return cleaned


def validate_variables(raw: object) -> list[dict]:
    if raw in (None, '', []):
        return []
    if not isinstance(raw, list):
        raise TemplateValidationError('Variables должны быть списком объектов.')

    items: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            raise TemplateValidationError('Variables должны быть списком объектов.')
        if is_empty_variable_placeholder(item):
            continue
        items.append(item)

    normalized: list[dict] = []
    seen_keys: set[str] = set()
    if len(items) > MAX_VARIABLES:
        raise TemplateValidationError(f'Максимум {MAX_VARIABLES} переменных в шаблоне.')

    for index, item in enumerate(items, start=1):
        extra_fields = set(item.keys()) - {'key', 'label', 'required', 'example'}
        forbidden = extra_fields & FORBIDDEN_VARIABLE_EXTRA_FIELDS
        if forbidden:
            raise TemplateValidationError(
                f'Переменная #{index} содержит запрещённые поля: {", ".join(sorted(forbidden))}.',
            )
        if extra_fields:
            raise TemplateValidationError(
                f'Переменная #{index} содержит неизвестные поля: {", ".join(sorted(extra_fields))}.',
            )

        key = validate_variable_key(str(item.get('key', '')))
        if key in seen_keys:
            raise TemplateValidationError(f'Key «{key}» повторяется в variables.')
        seen_keys.add(key)

        label = str(item.get('label', '')).strip()
        if not label:
            raise TemplateValidationError(f'Укажите label для переменной «{key}».')
        if len(label) > MAX_VARIABLE_LABEL_LENGTH:
            raise TemplateValidationError(f'Label переменной «{key}» слишком длинный.')

        required = bool(item.get('required', False))
        example = str(item.get('example', '')).strip()
        if len(example) > MAX_VARIABLE_EXAMPLE_LENGTH:
            raise TemplateValidationError(f'Example переменной «{key}» слишком длинный.')

        normalized.append({
            'key': key,
            'label': label,
            'required': required,
            'example': example,
        })

    return normalized


def validate_button_text(text: str, *, index: int) -> str:
    cleaned = (text or '').strip()
    if not cleaned:
        raise TemplateValidationError(f'Кнопка #{index}: укажите text.')
    if len(cleaned) > MAX_BUTTON_TEXT_LENGTH:
        raise TemplateValidationError(f'Кнопка #{index}: text слишком длинный.')
    if DANGEROUS_URL_SCHEME_PATTERN.match(cleaned):
        raise TemplateValidationError(f'Кнопка #{index}: недопустимое значение text.')
    return cleaned


def validate_button_url(value: str, *, index: int) -> str:
    cleaned = (value or '').strip()
    if not cleaned:
        raise TemplateValidationError(f'Кнопка #{index}: укажите URL.')
    if DANGEROUS_URL_SCHEME_PATTERN.match(cleaned):
        raise TemplateValidationError('URL кнопки должен начинаться с http:// или https://.')
    parsed = urlparse(cleaned)
    if parsed.scheme not in {'http', 'https'}:
        raise TemplateValidationError('URL кнопки должен начинаться с http:// или https://.')
    if len(cleaned) > MAX_BUTTON_VALUE_LENGTH:
        raise TemplateValidationError(f'Кнопка #{index}: URL слишком длинный.')
    return cleaned


def validate_button_phone(value: str, *, index: int) -> str:
    cleaned = re.sub(r'\D+', '', value or '')
    if not cleaned:
        raise TemplateValidationError(f'Кнопка #{index}: укажите публичный бизнес-номер.')
    if len(cleaned) < 10 or len(cleaned) > 15:
        raise TemplateValidationError('Номер кнопки должен быть публичным бизнес-номером.')
    return cleaned


def validate_button_quick_reply(value: str, *, index: int) -> str:
    cleaned = (value or '').strip()
    if not cleaned:
        raise TemplateValidationError(f'Кнопка #{index}: укажите value.')
    if len(cleaned) > MAX_BUTTON_VALUE_LENGTH:
        raise TemplateValidationError(f'Кнопка #{index}: value слишком длинный.')
    if DANGEROUS_URL_SCHEME_PATTERN.match(cleaned):
        raise TemplateValidationError(f'Кнопка #{index}: недопустимое value.')
    if '://' in cleaned:
        raise TemplateValidationError(f'Кнопка #{index}: quick_reply не может содержать URL.')
    return cleaned


def validate_buttons(raw: object) -> list[dict]:
    if raw in (None, '', []):
        return []
    if not isinstance(raw, list):
        raise TemplateValidationError('Buttons должны быть списком объектов.')

    items: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            raise TemplateValidationError('Buttons должны быть списком объектов.')
        if is_empty_button_placeholder(item):
            continue
        items.append(item)

    normalized: list[dict] = []
    if len(items) > MAX_BUTTONS:
        raise TemplateValidationError(f'Максимум {MAX_BUTTONS} кнопок в шаблоне.')

    for index, item in enumerate(items, start=1):
        extra_fields = set(item.keys()) - {'type', 'text', 'value'}
        if extra_fields:
            raise TemplateValidationError(
                f'Кнопка #{index} содержит неизвестные поля: {", ".join(sorted(extra_fields))}.',
            )

        button_type = str(item.get('type', '')).strip()
        if button_type not in BUTTON_TYPES:
            raise TemplateValidationError(f'Кнопка #{index}: недопустимый type.')

        text = validate_button_text(str(item.get('text', '')), index=index)
        value_raw = str(item.get('value', '')).strip()
        if button_type == BUTTON_TYPE_URL:
            value = validate_button_url(value_raw, index=index)
        elif button_type == BUTTON_TYPE_PHONE:
            value = validate_button_phone(value_raw, index=index)
        elif button_type == BUTTON_TYPE_QUICK_REPLY:
            value = validate_button_quick_reply(value_raw, index=index)
        else:
            value = value_raw

        normalized.append({
            'type': button_type,
            'text': text,
            'value': value,
        })

    return normalized


def raw_variables_from_post(post) -> list[dict]:
    indices: set[int] = set()
    for key in post.keys():
        if key.startswith('variable_key_'):
            suffix = key.removeprefix('variable_key_')
            if suffix.isdigit():
                indices.add(int(suffix))

    raw: list[dict] = []
    for index in sorted(indices):
        key = post.get(f'variable_key_{index}', '').strip()
        label = post.get(f'variable_label_{index}', '').strip()
        required = post.get(f'variable_required_{index}') == 'on'
        example = post.get(f'variable_example_{index}', '').strip()
        item = {
            'key': key,
            'label': label,
            'required': required,
            'example': example,
        }
        if is_empty_variable_placeholder(item):
            continue
        raw.append(item)
    return raw


def raw_buttons_from_post(post) -> list[dict]:
    indices: set[int] = set()
    for key in post.keys():
        if key.startswith('button_type_'):
            suffix = key.removeprefix('button_type_')
            if suffix.isdigit():
                indices.add(int(suffix))

    raw: list[dict] = []
    for index in sorted(indices):
        button_type = post.get(f'button_type_{index}', '').strip()
        text = post.get(f'button_text_{index}', '').strip()
        value = post.get(f'button_value_{index}', '').strip()
        item = {
            'type': button_type,
            'text': text,
            'value': value,
        }
        if is_empty_button_placeholder(item):
            continue
        raw.append(item)
    return raw


def variables_from_request_post(post) -> list[dict]:
    return validate_variables(raw_variables_from_post(post))


def buttons_from_request_post(post) -> list[dict]:
    return validate_buttons(raw_buttons_from_post(post))


def allowed_purposes_from_request_post(post) -> list[str]:
    return validate_allowed_purposes(post.getlist('allowed_purposes'))
