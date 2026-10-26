"""Scoped notification invitations; never sign in or claim a seller account."""
from urllib.parse import urlencode
from django.conf import settings
from django.core import signing
from django.urls import reverse
from core.models import RequestDispatch, SellerChannelConnection
from core.services.seller_request_access import find_seller_request_access

SALT = 'manual-seller-notifications-v1'


def invitation_access(token):
    access = find_seller_request_access(token)
    if not access or access.is_expired() or not access.seller_id or not access.request_id:
        return None
    seller, req = access.seller, access.request
    if (not seller.is_active or seller.is_paused or seller.catalog_only or req.access_revoked):
        return None
    if not RequestDispatch.objects.filter(request=req, seller=seller,
            delivery_channel=RequestDispatch.DELIVERY_MANUAL,
            status__in=[RequestDispatch.STATUS_PREPARED, RequestDispatch.STATUS_SENT]).exclude(permission_evidence='').exists():
        return None
    return access


def bot_url(access):
    return 'https://t.me/zpt_kz_zakaz_bot?start=invite_' + access.token


def settings_url(access, telegram_id):
    ticket = signing.dumps({'access': access.token, 'telegram': telegram_id}, salt=SALT)
    base = str(getattr(settings, 'PUBLIC_BASE_URL', 'https://zpt.kz')).rstrip('/')
    return base + reverse('seller_request_filters_link', args=[access.token]) + '?' + urlencode({'notifications': ticket})


def settings_connection(access, ticket):
    try:
        data = signing.loads(ticket, salt=SALT, max_age=72 * 3600)
    except (signing.BadSignature, TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get('access') != access.token or not invitation_access(access.token):
        return None
    return SellerChannelConnection.objects.filter(seller_id=access.seller_id, telegram_id=data.get('telegram')).exclude(telegram_id=None).first()
