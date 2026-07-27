from __future__ import annotations

import re
from typing import TYPE_CHECKING

from marketing.services.campaigns.send_constants import (
    FORBIDDEN_SAMPLE_ACCESS_TOKEN,
    VARIABLE_KEY_REQUEST_HISTORY_URL,
)
from marketing.services.templates.preview import render_template_preview_text

if TYPE_CHECKING:
    from marketing.models import MarketingWhatsAppTemplate

REQUEST_HISTORY_URL_PLACEHOLDER = '[Персональная ссылка на историю заявок]'

LANGUAGE_LABELS = {
    'ru': 'Русский',
    'kk': 'Қазақша',
    'en': 'English',
}

_UUID_IN_TEXT_PATTERN = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
    re.IGNORECASE,
)


def get_language_label(language_code: str) -> str:
    normalized = (language_code or '').strip().lower()
    return LANGUAGE_LABELS.get(normalized, language_code or '—')


def _safe_variable_example(variable: dict) -> str:
    key = (variable.get('key') or '').strip()
    example = (variable.get('example') or '').strip()
    if key == VARIABLE_KEY_REQUEST_HISTORY_URL:
        if not example:
            return REQUEST_HISTORY_URL_PLACEHOLDER
        if FORBIDDEN_SAMPLE_ACCESS_TOKEN in example:
            return REQUEST_HISTORY_URL_PLACEHOLDER
        if _UUID_IN_TEXT_PATTERN.search(example):
            return REQUEST_HISTORY_URL_PLACEHOLDER
        return example
    if example:
        return example
    return f'{{{{{key}}}}}'


def render_simple_mailing_template_preview(template: MarketingWhatsAppTemplate) -> dict:
    examples = {
        variable['key']: _safe_variable_example(variable)
        for variable in template.variables
        if variable.get('key')
    }

    base_preview = render_template_preview_text(template)
    if not examples:
        return base_preview

    import re as _re

    placeholder_pattern = _re.compile(r'\{\{\s*([a-z][a-z0-9_]*)\s*\}\}')

    def substitute(text: str) -> str:
        if not text:
            return ''

        def replacer(match: _re.Match[str]) -> str:
            key = match.group(1)
            return examples.get(key, match.group(0))

        return placeholder_pattern.sub(replacer, text)

    return {
        'header': substitute(template.header_text),
        'body': substitute(template.body_text),
        'footer': substitute(template.footer_text),
        'buttons': list(template.buttons),
    }
