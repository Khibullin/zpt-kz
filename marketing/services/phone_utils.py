from __future__ import annotations

from core.phone_utils import normalize_kz_phone
from core.services.buyer_contact_utils import mask_phone


def normalize_phone_key(raw_phone: object) -> str | None:
    """Return canonical 11-digit KZ phone key for aggregation."""
    return normalize_kz_phone(raw_phone)


__all__ = ['mask_phone', 'normalize_kz_phone', 'normalize_phone_key']
