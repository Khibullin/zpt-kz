from __future__ import annotations

from dataclasses import dataclass

from core.models import (
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    Seller,
)
from catalog.models import Product, SellerProfile
from marketing.services.contacts import (
    ROLE_DETAILING,
    ROLE_MARKETPLACE_BUYER,
    ROLE_MARKETPLACE_SELLER,
    ROLE_PARTS_BUYER,
    ROLE_PARTS_SELLER,
    ROLE_SERVICE_CUSTOMER,
    ROLE_STO,
    MarketingContact,
    _has_combined_seller_roles,
    build_contact_registry,
)
from marketing.services.audiences.constants import (
    GROUP_BUYERS,
    GROUP_SELLERS,
    GROUP_SERVICE_PROVIDERS,
    GROUP_TEST,
    SUBTYPE_ALL_BUYERS,
    SUBTYPE_ALL_SELLERS,
    SUBTYPE_ALL_SERVICE_PROVIDERS,
    SUBTYPE_COMBINED_SELLERS,
    SUBTYPE_DETAILING,
    SUBTYPE_MARKETPLACE_PAID,
    SUBTYPE_MARKETPLACE_SELLERS,
    SUBTYPE_PARTS_REQUESTS,
    SUBTYPE_REQUEST_SELLERS,
    SUBTYPE_SERVICE_REQUESTS,
    SUBTYPE_STO,
    SUBTYPE_TEST_CONTACTS,
)
from marketing.services.marketplace_orders import get_test_marketplace_phone_keys
from marketing.services.phone_utils import normalize_phone_key
from service_requests.models import ServiceSeller


@dataclass(frozen=True)
class SellerSourceFlags:
    is_parts_seller: bool = False
    is_marketplace_seller: bool = False
    is_sto: bool = False
    is_detailing: bool = False
    receive_requests: bool | None = None
    is_paused: bool | None = None
    has_products: bool = False
    has_active_products: bool = False
    has_logo: bool = False
    has_instagram: bool = False
    has_website: bool = False
    has_address: bool = False
    has_map_link: bool = False
    district: str = ''
    service_ids: frozenset[int] = frozenset()


def _build_seller_source_index() -> dict[str, SellerSourceFlags]:
    index: dict[str, dict] = {}

    def ensure(phone_key: str) -> dict:
        if phone_key not in index:
            index[phone_key] = {
                'is_parts_seller': False,
                'is_marketplace_seller': False,
                'is_sto': False,
                'is_detailing': False,
                'receive_requests': None,
                'is_paused': None,
                'has_products': False,
                'has_active_products': False,
                'has_logo': False,
                'has_instagram': False,
                'has_website': False,
                'has_address': False,
                'has_map_link': False,
                'district': '',
                'service_ids': set(),
            }
        return index[phone_key]

    for seller in Seller.objects.only(
        'whatsapp',
        'receive_requests',
        'is_paused',
    ):
        phone_key = normalize_phone_key(seller.whatsapp)
        if not phone_key:
            continue
        row = ensure(phone_key)
        row['is_parts_seller'] = True
        row['receive_requests'] = seller.receive_requests
        row['is_paused'] = seller.is_paused

    active_product_phones: set[str] = set()
    for row in Product.objects.exclude(whatsapp_number='').values('whatsapp_number', 'status'):
        phone_key = normalize_phone_key(row['whatsapp_number'])
        if not phone_key:
            continue
        entry = ensure(phone_key)
        entry['has_products'] = True
        if row['status'] == 'active':
            entry['has_active_products'] = True
            active_product_phones.add(phone_key)

    for profile in SellerProfile.objects.only(
        'phone',
        'logo',
        'instagram',
        'website',
    ):
        phone_key = normalize_phone_key(profile.phone)
        if not phone_key:
            continue
        row = ensure(phone_key)
        row['is_marketplace_seller'] = True
        row['has_logo'] = bool(profile.logo)
        row['has_instagram'] = bool(profile.instagram)
        row['has_website'] = bool(profile.website)

    for seller in ServiceSeller.objects.prefetch_related('services').only(
        'whatsapp',
        'seller_type',
        'receive_requests',
        'is_paused',
        'district',
        'address',
        'logo',
        'instagram',
        'website',
        'map_link',
    ):
        phone_key = normalize_phone_key(seller.whatsapp)
        if not phone_key:
            continue
        row = ensure(phone_key)
        if seller.seller_type == 'sto':
            row['is_sto'] = True
        else:
            row['is_detailing'] = True
        row['receive_requests'] = seller.receive_requests
        row['is_paused'] = seller.is_paused
        row['district'] = seller.district or ''
        row['has_address'] = bool(seller.address)
        row['has_logo'] = row['has_logo'] or bool(seller.logo)
        row['has_instagram'] = row['has_instagram'] or bool(seller.instagram)
        row['has_website'] = row['has_website'] or bool(seller.website)
        row['has_map_link'] = bool(seller.map_link)
        row['service_ids'].update(service.id for service in seller.services.all())

    return {
        phone_key: SellerSourceFlags(**payload)
        for phone_key, payload in index.items()
    }


def subtype_matches_group(contact_group: str, contact_subtype: str) -> bool:
    from marketing.services.audiences.constants import GROUP_SUBTYPE_MAP

    allowed = {item[0] for item in GROUP_SUBTYPE_MAP.get(contact_group, ())}
    return contact_subtype in allowed


def contact_matches_subtype(
    contact: MarketingContact,
    *,
    contact_group: str,
    contact_subtype: str,
) -> bool:
    if contact_group == GROUP_TEST:
        return contact_subtype == SUBTYPE_TEST_CONTACTS and contact.is_test

    if contact_group == GROUP_BUYERS:
        if contact_subtype == SUBTYPE_PARTS_REQUESTS:
            return ROLE_PARTS_BUYER in contact.roles
        if contact_subtype == SUBTYPE_MARKETPLACE_PAID:
            return ROLE_MARKETPLACE_BUYER in contact.roles
        if contact_subtype == SUBTYPE_SERVICE_REQUESTS:
            return ROLE_SERVICE_CUSTOMER in contact.roles
        if contact_subtype == SUBTYPE_ALL_BUYERS:
            buyer_roles = {ROLE_PARTS_BUYER, ROLE_MARKETPLACE_BUYER, ROLE_SERVICE_CUSTOMER}
            return bool(contact.roles & buyer_roles) and not contact.is_test
        return False

    if contact_group == GROUP_SELLERS:
        if contact.is_test:
            return False
        if contact_subtype == SUBTYPE_REQUEST_SELLERS:
            return ROLE_PARTS_SELLER in contact.roles
        if contact_subtype == SUBTYPE_MARKETPLACE_SELLERS:
            return ROLE_MARKETPLACE_SELLER in contact.roles
        if contact_subtype == SUBTYPE_COMBINED_SELLERS:
            return _has_combined_seller_roles(contact)
        if contact_subtype == SUBTYPE_ALL_SELLERS:
            return bool(
                contact.roles & {ROLE_PARTS_SELLER, ROLE_MARKETPLACE_SELLER},
            )
        return False

    if contact_group == GROUP_SERVICE_PROVIDERS:
        if contact.is_test:
            return False
        if contact_subtype == SUBTYPE_STO:
            return ROLE_STO in contact.roles
        if contact_subtype == SUBTYPE_DETAILING:
            return ROLE_DETAILING in contact.roles
        if contact_subtype == SUBTYPE_ALL_SERVICE_PROVIDERS:
            return bool(contact.roles & {ROLE_STO, ROLE_DETAILING})
        return False

    return False


def is_buyer_group(contact_group: str) -> bool:
    return contact_group == GROUP_BUYERS


def is_test_audience(contact_group: str, contact_subtype: str) -> bool:
    return contact_group == GROUP_TEST and contact_subtype == SUBTYPE_TEST_CONTACTS


def marketplace_test_phone_keys() -> frozenset[str]:
    return get_test_marketplace_phone_keys()


def build_registry() -> dict[str, MarketingContact]:
    return build_contact_registry()


def build_seller_source_index() -> dict[str, SellerSourceFlags]:
    return _build_seller_source_index()
