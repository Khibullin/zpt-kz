from __future__ import annotations


def normalize_kz_phone(raw_phone: object) -> str | None:
    """Return canonical KZ mobile number (11 digits, starts with 7) or None."""
    if raw_phone is None:
        return None
    if isinstance(raw_phone, bool) or not isinstance(raw_phone, (str, int, float)):
        return None
    digits = ''.join(ch for ch in str(raw_phone) if ch.isdigit())
    if not digits:
        return None
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    if len(digits) != 11 or not digits.startswith('7'):
        return None
    return digits
