from __future__ import annotations

from urllib.parse import quote


def _digits_only(raw_phone: object) -> str:
    if raw_phone is None:
        return ''
    if isinstance(raw_phone, bool) or not isinstance(raw_phone, (str, int, float)):
        return ''
    return ''.join(ch for ch in str(raw_phone) if ch.isdigit())


def normalize_kz_phone(raw_phone: object) -> str | None:
    """Return canonical KZ mobile number (11 digits, starts with 7) or None."""
    digits = _digits_only(raw_phone)
    if not digits:
        return None
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    if len(digits) != 11 or not digits.startswith('7'):
        return None
    return digits


def normalize_phone_for_whatsapp(raw_phone: object) -> str | None:
    """Return digits-only international phone for wa.me, or None if unsafe.

    Kazakhstan/Russia national 11-digit numbers starting with 8 are converted
    to country code 7. Other international numbers keep digits-only E.164 form.
    Ambiguous or truncated values do not produce a WhatsApp destination.
    """
    digits = _digits_only(raw_phone)
    if not digits:
        return None

    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    elif len(digits) == 10:
        digits = '7' + digits

    if len(digits) < 11 or len(digits) > 15:
        return None
    if digits.startswith(('0', '8')):
        return None
    # 11-digit destinations are only safe for KZ/RU (+7) and NANP (+1).
    # Other 11-digit values are usually truncated international numbers.
    if len(digits) == 11 and not digits.startswith(('1', '7')):
        return None
    if len(set(digits)) == 1:
        return None
    return digits


def build_whatsapp_url(phone: object, text: object = None) -> str:
    """Build https://wa.me/<digits> or return '' when the number is invalid."""
    digits = normalize_phone_for_whatsapp(phone)
    if not digits or is_excluded_city_whatsapp(digits):
        return ''
    url = f'https://wa.me/{digits}'
    message = str(text or '').strip()
    if message:
        url += f'?text={quote(message)}'
    return url


def is_excluded_city_whatsapp(raw_phone: object) -> bool:
    """Owner rule: old 327 and current 727 city numbers are calls only.

    Match the complete +7 area code, never digits inside a mobile number.
    Normalization stays usable for telephone display and account identity.
    """
    digits = normalize_phone_for_whatsapp(raw_phone)
    return bool(digits and len(digits) == 11 and digits[:4] in ('7327', '7727'))


def seller_whatsapp_phone(seller) -> str:
    """Select this card's next stored contact only when its primary is excluded.

    Does not rewrite the primary phone (also used for calls/account lookup),
    infer contacts from organization names, or check/send WhatsApp messages.
    """
    primary = getattr(seller, 'whatsapp', '')
    if not is_excluded_city_whatsapp(primary):
        return normalize_phone_for_whatsapp(primary) or ''
    candidates = [getattr(seller, 'phone2', '')]
    extras = getattr(seller, 'extra_phones', None)
    if extras is not None:
        candidates.extend(item.phone for item in extras.all())
    # ServiceSeller keeps imported contacts in an existing structured source row.
    # Parse only the contacts column, not arbitrary digits/IDs in the notes.
    import json
    import re
    notes = getattr(seller, 'catalog_notes', '') or ''
    start = notes.find('{')
    if start >= 0:
        try:
            row, _ = json.JSONDecoder().raw_decode(notes[start:])
        except (ValueError, TypeError):
            row = None
        if isinstance(row, dict):
            contacts = row.get('Контакты')
            if isinstance(contacts, str):
                candidates.extend(re.split(r'[,;\n|]', contacts))
    for candidate in candidates:
        digits = normalize_phone_for_whatsapp(candidate)
        if digits and not is_excluded_city_whatsapp(digits):
            return digits
    return ''


def seller_whatsapp_url(seller, text: object = None) -> str:
    return build_whatsapp_url(seller_whatsapp_phone(seller), text)
