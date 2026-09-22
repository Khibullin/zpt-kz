"""Robokassa KZ signature helpers. Never log bases that include passwords."""

from __future__ import annotations

import hashlib
import hmac
import re

SHP_NAME_RE = re.compile(r'^Shp_[A-Za-z0-9_]+$')


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def signatures_equal(left: str, right: str) -> bool:
    a = str(left or '').strip().lower()
    b = str(right or '').strip().lower()
    if not a or not b or len(a) != len(b):
        return False
    try:
        left_bytes = a.encode('ascii')
        right_bytes = b.encode('ascii')
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(left_bytes, right_bytes)


def sorted_shp_items(params):
    items = []
    for key, value in params.items():
        if not str(key).startswith('Shp_'):
            continue
        if not SHP_NAME_RE.match(str(key)):
            raise ValueError('invalid_shp_name')
        items.append((str(key), str(value)))
    items.sort(key=lambda item: item[0])
    return items


def shp_signature_suffix(params) -> str:
    items = sorted_shp_items(params)
    if not items:
        return ''
    return ''.join(f':{key}={value}' for key, value in items)


def init_signature_base(merchant_login, out_sum, inv_id, password, params=None):
    base = f'{merchant_login}:{out_sum}:{inv_id}:{password}'
    if params:
        base += shp_signature_suffix(params)
    return base


def result_signature_base(out_sum, inv_id, password, params=None):
    base = f'{out_sum}:{inv_id}:{password}'
    if params:
        base += shp_signature_suffix(params)
    return base


def success_signature_base(out_sum, inv_id, password, params=None):
    return result_signature_base(out_sum, inv_id, password, params=params)


def sign_init(merchant_login, out_sum, inv_id, password, params=None) -> str:
    return sha256_hex(
        init_signature_base(merchant_login, out_sum, inv_id, password, params)
    )


def sign_result(out_sum, inv_id, password, params=None) -> str:
    return sha256_hex(
        result_signature_base(out_sum, inv_id, password, params)
    )


def sign_success(out_sum, inv_id, password, params=None) -> str:
    return sha256_hex(
        success_signature_base(out_sum, inv_id, password, params)
    )
