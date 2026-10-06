"""ServiceSeller login identity.

auth.User is the password master once a ServiceSeller is linked.
This module does not create catalog.SellerProfile or core.Seller.
"""

from __future__ import annotations

from django.contrib.auth import authenticate, get_user_model, login
from django.contrib.auth.hashers import check_password
from django.db import IntegrityError, transaction

from core.services.seller_identity import find_users_by_phone, normalize_seller_whatsapp
from service_requests.models import ServiceSeller

User = get_user_model()

_MODEL_BACKEND = 'django.contrib.auth.backends.ModelBackend'


class _ServiceAuthRejected(Exception):
    """Abort a linking transaction and return a generic auth failure."""


class ServiceSellerAccessDenied(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def get_current_service_seller(request):
    """Return the ServiceSeller owned by the authenticated user.

    Request parameters are never used as identity.
    """
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        raise ServiceSellerAccessDenied(401, 'Требуется вход')
    try:
        return user.service_seller_profile
    except ServiceSeller.DoesNotExist as exc:
        raise ServiceSellerAccessDenied(403, 'Доступ запрещён') from exc


def authenticate_service_seller(request, whatsapp, password):
    """Return the ServiceSeller after a real Django login, or None."""
    try:
        return _authenticate_service_seller(request, whatsapp, password)
    except (_ServiceAuthRejected, IntegrityError, ServiceSeller.DoesNotExist):
        return None


def _authenticate_service_seller(request, whatsapp, password):
    phone = normalize_seller_whatsapp(whatsapp)
    password = password if isinstance(password, str) else ''
    if not phone or not password:
        return None

    sellers = _service_sellers_for_phone(phone)
    if len(sellers) != 1:
        return None
    seller = sellers[0]

    if seller.user_id:
        return _login_linked_seller(request, seller.pk, phone, password)

    users = find_users_by_phone(phone)
    if len(users) > 1:
        return None
    if len(users) == 1:
        authed = authenticate(request, username=users[0].get_username(), password=password)
        if authed is None:
            return None
        return _link_existing_user(request, seller.pk, phone, authed)

    if not _legacy_password_matches(seller, password):
        return None
    return _migrate_legacy_seller(request, seller.pk, phone, password)


def _service_sellers_for_phone(phone: str) -> list[ServiceSeller]:
    """Every ServiceSeller whose stored WhatsApp normalizes to phone.

    Raw strings are not identity. Two different stored formats of one
    number are ambiguity: the caller must not pick .first().
    """
    matched_ids = [
        pk
        for pk, raw in ServiceSeller.objects.values_list('pk', 'whatsapp')
        if normalize_seller_whatsapp(raw) == phone
    ]
    if not matched_ids:
        return []
    return list(
        ServiceSeller.objects.filter(pk__in=matched_ids).select_related('user')
    )


def _login_linked_seller(request, seller_id, phone, password):
    with transaction.atomic():
        seller = _lock_unique_seller(seller_id, phone)
        user = seller.user
        if user is None:
            raise _ServiceAuthRejected
        authed = authenticate(request, username=user.get_username(), password=password)
        if authed is None:
            raise _ServiceAuthRejected
        _store_cleared_legacy_password(seller)
    _establish_session(request, authed)
    return seller


def _link_existing_user(request, seller_id, phone, user):
    with transaction.atomic():
        seller = _lock_unique_seller(seller_id, phone)
        if seller.user_id and seller.user_id != user.pk:
            raise _ServiceAuthRejected
        if seller.user_id is None:
            _reject_if_user_taken(seller, user)
            seller.user = user
        seller.password = ''
        seller.save(update_fields=['user', 'password'])
    _establish_session(request, user)
    return seller


def _migrate_legacy_seller(request, seller_id, phone, password):
    session_user = None
    try:
        with transaction.atomic():
            seller = _lock_unique_seller(seller_id, phone)
            if seller.user_id:
                session_user = authenticate(
                    request,
                    username=seller.user.get_username(),
                    password=password,
                )
                if session_user is None:
                    raise _ServiceAuthRejected
                _store_cleared_legacy_password(seller)
            else:
                users = find_users_by_phone(phone)
                if len(users) > 1:
                    raise _ServiceAuthRejected
                if len(users) == 1:
                    session_user = authenticate(
                        request,
                        username=users[0].get_username(),
                        password=password,
                    )
                    if session_user is None:
                        raise _ServiceAuthRejected
                    _reject_if_user_taken(seller, session_user)
                    seller.user = session_user
                else:
                    if not _legacy_password_matches(seller, password):
                        raise _ServiceAuthRejected
                    session_user = User.objects.create_user(
                        username=phone,
                        password=password,
                    )
                    _reject_if_user_taken(seller, session_user)
                    seller.user = session_user
                seller.password = ''
                seller.save(update_fields=['user', 'password'])
    except IntegrityError:
        return _retry_after_conflict(request, seller_id, phone, password)

    _establish_session(request, session_user)
    return ServiceSeller.objects.select_related('user').get(pk=seller_id)


def _retry_after_conflict(request, seller_id, phone, password):
    users = find_users_by_phone(phone)
    if len(users) != 1:
        return None
    authed = authenticate(request, username=users[0].get_username(), password=password)
    if authed is None:
        return None
    try:
        return _link_existing_user(request, seller_id, phone, authed)
    except (_ServiceAuthRejected, IntegrityError, ServiceSeller.DoesNotExist):
        return None


def _lock_unique_seller(seller_id, phone) -> ServiceSeller:
    seller = (
        ServiceSeller.objects.select_for_update()
        .select_related('user')
        .get(pk=seller_id)
    )
    if normalize_seller_whatsapp(seller.whatsapp) != phone:
        raise _ServiceAuthRejected
    matches = _service_sellers_for_phone(phone)
    if len(matches) != 1 or matches[0].pk != seller.pk:
        raise _ServiceAuthRejected
    return seller


def _reject_if_user_taken(seller, user):
    if ServiceSeller.objects.filter(user=user).exclude(pk=seller.pk).exists():
        raise IntegrityError('ServiceSeller user is already linked')


def _legacy_password_matches(seller, password) -> bool:
    encoded = seller.password or ''
    if not encoded:
        return False
    return check_password(password, encoded)


def _store_cleared_legacy_password(seller):
    if not seller.password:
        return
    seller.password = ''
    seller.save(update_fields=['password'])


def _establish_session(request, user):
    if user is None:
        raise _ServiceAuthRejected
    if not getattr(user, 'backend', None):
        user.backend = _MODEL_BACKEND
    login(request, user)
