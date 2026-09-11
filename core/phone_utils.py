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
    if not digits:
        return ''
    url = f'https://wa.me/{digits}'
    message = str(text or '').strip()
    if message:
        url += f'?text={quote(message)}'
    return url
