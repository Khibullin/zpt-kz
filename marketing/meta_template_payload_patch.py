from __future__ import annotations

import re

from core import whatsapp_template_management as meta_management

_POSITIONAL_PLACEHOLDER_RE = re.compile(r"\{\{(\d+)\}\}")
_ORIGINAL_BUILD_META_TEMPLATE_PAYLOAD = meta_management.build_meta_template_payload


def _body_examples(template, body_text: str) -> list[str]:
    indexes = [int(value) for value in _POSITIONAL_PLACEHOLDER_RE.findall(body_text or '')]
    if not indexes:
        return []

    expected_count = max(indexes)
    variables = getattr(template, 'variables', None)
    if not isinstance(variables, list) or len(variables) < expected_count:
        return []

    examples: list[str] = []
    for position in range(expected_count):
        item = variables[position]
        if not isinstance(item, dict):
            return []
        example = str(item.get('example') or '').strip()
        if not example:
            return []
        examples.append(example)
    return examples


def build_meta_template_payload_with_examples(template) -> dict:
    """Extend the existing Meta payload with BODY examples for positional variables.

    Meta's template-creation API expects example.body_text when a BODY contains
    placeholders such as {{1}}, {{2}}, etc. Existing templates without variables
    remain byte-for-byte equivalent apart from normal dict identity.
    """
    payload = _ORIGINAL_BUILD_META_TEMPLATE_PAYLOAD(template)
    body_text = str(getattr(template, 'body_text', '') or '')
    examples = _body_examples(template, body_text)
    if not examples:
        return payload

    for component in payload.get('components') or []:
        if str(component.get('type') or '').upper() != 'BODY':
            continue
        component['example'] = {'body_text': [examples]}
        break
    return payload


def install_meta_template_payload_patch() -> None:
    current = meta_management.build_meta_template_payload
    if getattr(current, '_zpt_body_examples_patch', False):
        return
    build_meta_template_payload_with_examples._zpt_body_examples_patch = True
    meta_management.build_meta_template_payload = build_meta_template_payload_with_examples
