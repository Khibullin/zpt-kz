"""Robokassa KZ stage-1 settings. Live payments are never allowed here."""

from __future__ import annotations

from django.conf import settings

from catalog.models import SellerProfile

MERCHANT_LOGIN_DEFAULT = 'zptkz'
PAYMENT_URL = 'https://auth.robokassa.kz/Merchant/Index.aspx'
HASH_ALGO_SHA256 = 'sha256'
CURRENCY_KZT = 'KZT'
MODE_TEST = 'test'
INVID_MIN = 1
INVID_MAX = 2147483647


def _flag(name, default=False):
    value = getattr(settings, name, default)
    if isinstance(value, str):
        return value.strip().lower() in ('true', '1', 'yes')
    return bool(value)


def _text(name, default=''):
    return str(getattr(settings, name, default) or default).strip()


def merchant_login():
    return _text('ROBOKASSA_MERCHANT_LOGIN', MERCHANT_LOGIN_DEFAULT) or MERCHANT_LOGIN_DEFAULT


def payment_url():
    return PAYMENT_URL


def hash_algo_test():
    algo = _text('ROBOKASSA_HASH_ALGO_TEST', HASH_ALGO_SHA256).lower()
    return algo or HASH_ALGO_SHA256


def test_password_1():
    return _text('ROBOKASSA_PASS1_TEST')


def test_password_2():
    return _text('ROBOKASSA_PASS2_TEST')


def integration_enabled():
    return _flag('ROBOKASSA_ENABLED', False)


def test_start_enabled():
    return _flag('ROBOKASSA_TEST_ENABLED', False)


def live_enabled_setting():
    """Recorded setting only. Stage 1 never honours a true value."""
    return _flag('ROBOKASSA_LIVE_ENABLED', False)


def live_payments_allowed():
    return False


def own_seller_profile_id_raw():
    return _text('ROBOKASSA_OWN_SELLER_PROFILE_ID')


def parse_own_seller_profile_id():
    raw = own_seller_profile_id_raw()
    if not raw or not raw.isdigit():
        return None
    value = int(raw)
    if value < 1:
        return None
    return value


def resolve_own_seller_profile():
    seller_id = parse_own_seller_profile_id()
    if seller_id is None:
        return None
    return SellerProfile.objects.filter(pk=seller_id).first()
