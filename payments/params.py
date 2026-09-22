"""Parse Robokassa callback parameters without guessing duplicates."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .config import INVID_MAX, INVID_MIN
from .exceptions import CallbackRejected
from .signatures import SHP_NAME_RE

REQUIRED_KEYS = ('OutSum', 'InvId', 'SignatureValue')


def _values(querydict, name):
    if not querydict:
        return []
    return [str(value) for value in querydict.getlist(name)]


def extract_single(request, name):
    post_values = _values(getattr(request, 'POST', None), name)
    get_values = _values(getattr(request, 'GET', None), name)
    if len(post_values) > 1 or len(get_values) > 1:
        raise CallbackRejected('ambiguous_param')
    if post_values and get_values:
        raise CallbackRejected('ambiguous_param')
    if post_values:
        return post_values[0]
    if get_values:
        return get_values[0]
    return None


def collect_shp_params(request):
    names = set()
    for querydict in (getattr(request, 'POST', None), getattr(request, 'GET', None)):
        if not querydict:
            continue
        for key in querydict.keys():
            if str(key).startswith('Shp_'):
                names.add(str(key))
    params = {}
    for name in names:
        if not SHP_NAME_RE.match(name):
            raise CallbackRejected('invalid_shp')
        params[name] = extract_single(request, name)
    return params


def extract_callback_strings(request):
    values = {}
    missing = []
    for name in REQUIRED_KEYS:
        value = extract_single(request, name)
        if value is None or str(value) == '':
            missing.append(name)
        else:
            values[name] = str(value)
    if missing:
        raise CallbackRejected('missing_param')
    shp = collect_shp_params(request)
    return values['OutSum'], values['InvId'], values['SignatureValue'], shp


def parse_inv_id_string(raw):
    text = str(raw or '')
    if not text or not text.isdigit():
        raise CallbackRejected('bad_invid')
    value = int(text)
    if value < INVID_MIN or value > INVID_MAX:
        raise CallbackRejected('bad_invid')
    return value


def parse_out_sum_decimal(raw):
    text = str(raw or '').strip()
    lowered = text.lower()
    if not text or any(token in lowered for token in ('nan', 'inf', 'infinity')):
        raise CallbackRejected('bad_sum')
    if 'e' in lowered:
        raise CallbackRejected('bad_sum')
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise CallbackRejected('bad_sum') from exc
    if not amount.is_finite() or amount <= 0:
        raise CallbackRejected('bad_sum')
    return amount
