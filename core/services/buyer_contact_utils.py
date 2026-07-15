from __future__ import annotations

import re


def normalize_buyer_text(value: object) -> str:
    if value is None:
        return ''
    text = str(value).strip()
    text = re.sub(r'\s+', ' ', text)
    return text.casefold()


def mask_phone(phone: str) -> str:
    digits = ''.join(ch for ch in str(phone or '') if ch.isdigit())
    if len(digits) < 6:
        return '***'
    return f'{digits[:4]}***{digits[-4:]}'
