"""Unified seller identity for request cabinet and marketplace shop."""

from __future__ import annotations

from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.hashers import check_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q

from catalog.models import Product, SellerProfile
from core.models import Seller
from core.whatsapp_template_sender import normalize_whatsapp_phone

User = get_user_model()

AUTH_FAILED = 'Неверный WhatsApp или пароль'
PHONE_TAKEN = 'Продавец с таким номером WhatsApp уже зарегистрирован'
PHONE_CONFLICT = 'Этот WhatsApp уже используется другим аккаунтом.'
MIN_PASSWORD_LENGTH = 8
REMEMBER_ME_SECONDS = 1209600


class SellerIdentityError(Exception):
    def __init__(self, message, *, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def normalize_seller_whatsapp(phone) -> str:
    return normalize_whatsapp_phone(phone)


def phone_lookup_variants(phone) -> set[str]:
    digits = normalize_seller_whatsapp(phone)
    variants = {digits} if digits else set()
    if digits:
        variants.add('+' + digits)
        if len(digits) == 11 and digits.startswith('8'):
            variants.add('7' + digits[1:])
            variants.add('+7' + digits[1:])
        if len(digits) == 11 and digits.startswith('7'):
            variants.add('8' + digits[1:])
    return {item for item in variants if item}


def find_users_by_phone(phone) -> list:
    digits = normalize_seller_whatsapp(phone)
    if not digits:
        return []
    variants = phone_lookup_variants(digits)
    users = list(
        User.objects.filter(
            Q(username__in=variants) | Q(seller_profile__phone__in=variants)
        ).distinct()
    )
    seen = {user.pk for user in users}
    extra = []
    for user in users:
        if normalize_seller_whatsapp(user.username) == digits and user.pk not in seen:
            extra.append(user)
            seen.add(user.pk)
    return users + extra


def find_unique_user_by_phone(phone):
    users = find_users_by_phone(phone)
    if len(users) == 1:
        return users[0]
    return None


def find_sellers_by_phone(phone) -> list[Seller]:
    digits = normalize_seller_whatsapp(phone)
    if not digits:
        return []
    variants = phone_lookup_variants(digits)
    sellers = list(Seller.objects.filter(whatsapp__in=variants))
    matched = []
    seen = set()
    for seller in sellers:
        if normalize_seller_whatsapp(seller.whatsapp) == digits and seller.pk not in seen:
            matched.append(seller)
            seen.add(seller.pk)
        elif seller.whatsapp in variants and seller.pk not in seen:
            matched.append(seller)
            seen.add(seller.pk)
    return matched


def find_unique_seller_by_phone(phone):
    sellers = find_sellers_by_phone(phone)
    if len(sellers) == 1:
        return sellers[0]
    return None


def find_profiles_by_phone(phone) -> list[SellerProfile]:
    digits = normalize_seller_whatsapp(phone)
    if not digits:
        return []
    variants = phone_lookup_variants(digits)
    return list(SellerProfile.objects.filter(phone__in=variants).select_related('user'))


def login_phone_in_use(
    phone,
    *,
    exclude_user_id=None,
    exclude_seller_id=None,
    exclude_profile_id=None,
) -> bool:
    digits = normalize_seller_whatsapp(phone)
    if not digits:
        return False
    variants = phone_lookup_variants(digits)

    users = User.objects.filter(username__in=variants)
    if exclude_user_id:
        users = users.exclude(pk=exclude_user_id)
    if users.exists():
        return True

    sellers = Seller.objects.filter(whatsapp__in=variants)
    if exclude_seller_id:
        sellers = sellers.exclude(pk=exclude_seller_id)
    if sellers.exists():
        return True

    profiles = SellerProfile.objects.filter(phone__in=variants)
    if exclude_profile_id:
        profiles = profiles.exclude(pk=exclude_profile_id)
    if profiles.exists():
        return True

    return False


def clear_legacy_password(seller: Seller) -> None:
    fields = []
    if seller.password_hash:
        seller.password_hash = ''
        fields.append('password_hash')
    if seller.must_change_password:
        seller.must_change_password = False
        fields.append('must_change_password')
    if fields:
        seller.save(update_fields=fields)


def link_seller_to_user(seller: Seller, user) -> Seller:
    if seller.user_id == user.pk:
        clear_legacy_password(seller)
        return seller
    if seller.user_id and seller.user_id != user.pk:
        raise SellerIdentityError(PHONE_CONFLICT)
    other = Seller.objects.filter(user=user).exclude(pk=seller.pk).first()
    if other:
        raise SellerIdentityError(PHONE_CONFLICT)
    seller.user = user
    seller.password_hash = ''
    seller.must_change_password = False
    seller.save(update_fields=['user', 'password_hash', 'must_change_password'])
    return seller


def ensure_seller_profile_for_user(user, *, name='', phone='', city='', extra=None) -> SellerProfile:
    profile = SellerProfile.objects.filter(user=user).first()
    if profile:
        return profile
    digits = normalize_seller_whatsapp(phone) or normalize_seller_whatsapp(user.username)
    payload = {
        'user': user,
        'name': (name or user.get_username() or 'Продавец')[:255],
        'phone': (digits or user.get_username())[:30],
        'city': (city or '')[:120],
    }
    if extra:
        payload.update(extra)
    return SellerProfile.objects.create(**payload)


def ensure_request_seller_for_user(
    user,
    *,
    profile=None,
    name='',
    whatsapp='',
    city='',
    transport_type='car',
) -> Seller | None:
    seller = Seller.objects.filter(user=user).first()
    if seller:
        clear_legacy_password(seller)
        return seller

    profile = profile or SellerProfile.objects.filter(user=user).first()
    digits = (
        normalize_seller_whatsapp(whatsapp)
        or (normalize_seller_whatsapp(profile.phone) if profile else '')
        or normalize_seller_whatsapp(user.username)
    )
    display_name = name or (profile.name if profile else '') or user.get_username()
    display_city = city or (profile.city if profile else '') or ''

    unlinked = [
        item for item in find_sellers_by_phone(digits)
        if not item.user_id
    ] if digits else []
    if len(unlinked) == 1:
        return link_seller_to_user(unlinked[0], user)
    if len(unlinked) > 1:
        return None

    linked_elsewhere = [
        item for item in find_sellers_by_phone(digits)
        if item.user_id and item.user_id != user.pk
    ] if digits else []
    if linked_elsewhere:
        return None
    if not digits:
        return None

    return Seller.objects.create(
        user=user,
        name=display_name[:255],
        whatsapp=digits[:20],
        password_hash='',
        must_change_password=False,
        seller_type='seller',
        transport_type=transport_type or 'car',
        city=display_city[:100],
        is_active=True,
        is_paused=False,
        receive_requests=False,
        is_test_seller=False,
    )


def establish_unified_session(request, user, seller, *, remember_me=None) -> None:
    if not getattr(user, 'backend', None):
        user.backend = 'django.contrib.auth.backends.ModelBackend'
    login(request, user)
    if remember_me is True:
        request.session.set_expiry(REMEMBER_ME_SECONDS)
    elif remember_me is False:
        request.session.set_expiry(0)
    request.session['seller_id'] = seller.id


def provision_authenticated_user(request, user, *, remember_me=None):
    profile = ensure_seller_profile_for_user(
        user,
        name=user.get_username(),
        phone=user.username,
    )
    seller = ensure_request_seller_for_user(user, profile=profile)
    if seller is None:
        raise SellerIdentityError(AUTH_FAILED)
    establish_unified_session(request, user, seller, remember_me=remember_me)
    return user, seller, profile


def provision_legacy_seller(request, seller: Seller, plaintext_password: str, *, remember_me=None):
    phone = normalize_seller_whatsapp(seller.whatsapp)
    if not phone:
        raise SellerIdentityError(AUTH_FAILED)
    if find_users_by_phone(phone):
        raise SellerIdentityError(AUTH_FAILED)
    try:
        with transaction.atomic():
            user = User.objects.create_user(username=phone, password=plaintext_password)
            ensure_seller_profile_for_user(
                user,
                name=seller.name,
                phone=phone,
                city=seller.city,
            )
            link_seller_to_user(seller, user)
    except IntegrityError as exc:
        raise SellerIdentityError(AUTH_FAILED) from exc
    return provision_authenticated_user(request, user, remember_me=remember_me)


def authenticate_unified_seller(request, whatsapp, password, *, remember_me=None):
    phone = normalize_seller_whatsapp(whatsapp)
    password = password or ''
    if not phone or not password:
        raise SellerIdentityError(AUTH_FAILED)

    users = find_users_by_phone(phone)
    if len(users) > 1:
        raise SellerIdentityError(AUTH_FAILED)
    if len(users) == 1:
        authed = authenticate(request, username=users[0].username, password=password)
        if authed is None:
            raise SellerIdentityError(AUTH_FAILED)
        return provision_authenticated_user(request, authed, remember_me=remember_me)

    seller = find_unique_seller_by_phone(phone)
    if seller is None or not seller.password_hash:
        raise SellerIdentityError(AUTH_FAILED)
    if not check_password(password, seller.password_hash):
        raise SellerIdentityError(AUTH_FAILED)
    return provision_legacy_seller(request, seller, password, remember_me=remember_me)


def authenticate_shop_seller(request, username, password, *, remember_me=False):
    username = (username or '').strip()
    password = password or ''
    if not username or not password:
        return None

    user = authenticate(request, username=username, password=password)
    if user is None:
        phone = normalize_seller_whatsapp(username)
        if phone and phone != username:
            user = authenticate(request, username=phone, password=password)
        if user is None and phone:
            unique = find_unique_user_by_phone(phone)
            if unique is not None:
                user = authenticate(request, username=unique.username, password=password)
    if user is None:
        return None
    try:
        provision_authenticated_user(request, user, remember_me=remember_me)
    except SellerIdentityError:
        if not getattr(user, 'backend', None):
            user.backend = 'django.contrib.auth.backends.ModelBackend'
        login(request, user)
        if remember_me is True:
            request.session.set_expiry(REMEMBER_ME_SECONDS)
        elif remember_me is False:
            request.session.set_expiry(0)
    return user


def create_unified_seller_account(
    *,
    name,
    whatsapp,
    password,
    city='',
    transport_type='car',
    seller_defaults=None,
    profile_defaults=None,
):
    phone = normalize_seller_whatsapp(whatsapp)
    name = (name or '').strip()
    if not name:
        raise SellerIdentityError('Укажите название продавца')
    if not phone:
        raise SellerIdentityError('Укажите WhatsApp')
    if login_phone_in_use(phone):
        raise SellerIdentityError(PHONE_TAKEN)
    if len(password or '') < MIN_PASSWORD_LENGTH:
        raise SellerIdentityError(
            f'Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов'
        )
    try:
        validate_password(password, User(username=phone))
    except ValidationError as exc:
        raise SellerIdentityError(' '.join(exc.messages)) from exc

    seller_payload = {
        'name': name[:255],
        'whatsapp': phone[:20],
        'password_hash': '',
        'must_change_password': False,
        'seller_type': 'seller',
        'transport_type': transport_type or 'car',
        'city': (city or '')[:100],
        'is_active': True,
        'is_paused': False,
        'receive_requests': False,
        'is_test_seller': False,
    }
    if seller_defaults:
        seller_payload.update(seller_defaults)
        seller_payload['password_hash'] = ''
        seller_payload.pop('user', None)
    seller_payload['receive_requests'] = False

    profile_payload = dict(profile_defaults or {})

    with transaction.atomic():
        user = User.objects.create_user(username=phone, password=password)
        profile = SellerProfile.objects.create(
            user=user,
            name=name[:255],
            phone=phone[:30],
            city=(city or '')[:120],
            **profile_payload,
        )
        seller = Seller.objects.create(user=user, **seller_payload)
    return user, seller, profile


def sync_login_phone(*, user, new_phone, seller=None, profile=None) -> str:
    phone = normalize_seller_whatsapp(new_phone)
    if not phone:
        raise SellerIdentityError('Укажите WhatsApp')
    seller = seller or Seller.objects.filter(user=user).first()
    profile = profile or SellerProfile.objects.filter(user=user).first()
    if login_phone_in_use(
        phone,
        exclude_user_id=getattr(user, 'pk', None),
        exclude_seller_id=getattr(seller, 'pk', None),
        exclude_profile_id=getattr(profile, 'pk', None),
    ):
        raise SellerIdentityError(PHONE_CONFLICT)

    with transaction.atomic():
        if user.username != phone:
            user.username = phone[:150]
            user.save(update_fields=['username'])
        if seller is not None and seller.whatsapp != phone[:20]:
            seller.whatsapp = phone[:20]
            seller.save(update_fields=['whatsapp'])
        if profile is not None and profile.phone != phone[:30]:
            profile.phone = phone[:30]
            profile.save(update_fields=['phone'])
    return phone


def change_unified_seller_password(request, user, old_password, new_password, new_password_confirm):
    if user is None:
        raise SellerIdentityError('Требуется вход продавца', status=401)
    if not user.check_password(old_password or ''):
        raise SellerIdentityError('Старый пароль неверный')
    if (new_password or '') != (new_password_confirm or ''):
        raise SellerIdentityError('Новые пароли не совпадают')
    if len(new_password or '') < MIN_PASSWORD_LENGTH:
        raise SellerIdentityError(
            f'Новый пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов'
        )
    try:
        validate_password(new_password, user)
    except ValidationError as exc:
        raise SellerIdentityError(' '.join(exc.messages)) from exc
    user.set_password(new_password)
    user.save(update_fields=['password'])
    seller = Seller.objects.filter(user=user).first()
    if seller is not None:
        clear_legacy_password(seller)
    if getattr(request.user, 'is_authenticated', False) and request.user.pk == user.pk:
        update_session_auth_hash(request, user)
    return user


def admin_reset_seller_password(seller: Seller, new_password: str) -> User:
    if len(new_password or '') < MIN_PASSWORD_LENGTH:
        raise SellerIdentityError(
            f'Пароль должен содержать минимум {MIN_PASSWORD_LENGTH} символов.'
        )
    phone = normalize_seller_whatsapp(seller.whatsapp)
    if not phone:
        raise SellerIdentityError('У продавца не указан WhatsApp.')

    with transaction.atomic():
        user = seller.user
        if user is None:
            candidates = find_users_by_phone(phone)
            if len(candidates) > 1:
                raise SellerIdentityError(PHONE_CONFLICT)
            if len(candidates) == 1:
                existing = Seller.objects.filter(user=candidates[0]).exclude(pk=seller.pk).first()
                if existing:
                    raise SellerIdentityError(PHONE_CONFLICT)
                user = candidates[0]
            else:
                user = User(username=phone)
                try:
                    validate_password(new_password, user)
                except ValidationError as exc:
                    raise SellerIdentityError(' '.join(exc.messages)) from exc
                user = User.objects.create_user(username=phone, password=new_password)
        try:
            validate_password(new_password, user)
        except ValidationError as exc:
            raise SellerIdentityError(' '.join(exc.messages)) from exc
        if not user.check_password(new_password):
            user.set_password(new_password)
            user.save(update_fields=['password'])
        ensure_seller_profile_for_user(
            user,
            name=seller.name,
            phone=phone,
            city=seller.city,
        )
        link_seller_to_user(seller, user)
    return user


def logout_unified_seller(request) -> None:
    if hasattr(request, 'session'):
        request.session.pop('seller_id', None)
    logout(request)


def get_logged_request_seller(request):
    seller_id = request.session.get('seller_id') if hasattr(request, 'session') else None
    if seller_id:
        seller = Seller.objects.filter(id=seller_id).first()
        if seller:
            return seller
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        seller = Seller.objects.filter(user=user).first()
        if seller and hasattr(request, 'session'):
            request.session['seller_id'] = seller.id
        return seller
    return None


def delete_unified_seller_account(user, profile: SellerProfile) -> None:
    seller = Seller.objects.filter(user=user).first()
    Product.objects.owned_by_seller(profile).delete()
    profile.delete()
    if seller is not None:
        seller.password_hash = ''
        seller.user = None
        seller.save(update_fields=['password_hash', 'user'])
        seller.delete()
    user.delete()
