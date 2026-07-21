from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from marketing.models import MarketingWhatsAppTemplate

_PLACEHOLDER_PATTERN = re.compile(r'\{\{\s*([a-z][a-z0-9_]*)\s*\}\}')


def render_template_preview_text(template: MarketingWhatsAppTemplate) -> dict:
    examples = {
        variable['key']: variable.get('example') or f'{{{{{variable["key"]}}}}}'
        for variable in template.variables
    }

    def substitute(text: str) -> str:
        if not text:
            return ''

        def replacer(match: re.Match[str]) -> str:
            key = match.group(1)
            return examples.get(key, match.group(0))

        return _PLACEHOLDER_PATTERN.sub(replacer, text)

    return {
        'header': substitute(template.header_text),
        'body': substitute(template.body_text),
        'footer': substitute(template.footer_text),
        'buttons': list(template.buttons),
    }
