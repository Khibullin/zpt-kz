from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from marketing.models import MarketingAudience


def _normalize_signature_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _normalize_signature_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        normalized_items = [_normalize_signature_value(item) for item in value]
        return sorted(
            normalized_items,
            key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False),
        )
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def audience_signature_payload(audience: MarketingAudience) -> dict[str, Any]:
    return {
        'contact_group': audience.contact_group,
        'contact_subtype': audience.contact_subtype,
        'criteria': _normalize_signature_value(audience.criteria or {}),
        'is_active': audience.is_active,
    }


def compute_audience_signature(audience: MarketingAudience) -> str:
    payload = audience_signature_payload(audience)
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()
