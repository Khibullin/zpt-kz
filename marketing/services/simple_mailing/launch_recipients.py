from __future__ import annotations

from dataclasses import dataclass

from core.models import BuyerContact, Request, Seller
from core.services.buyer_contact_utils import normalize_buyer_text
from marketing.services.marketplace_orders import get_marketplace_buyer_counts
from marketing.services.phone_utils import normalize_phone_key
from marketing.services.simple_mailing.brands import (
    build_exclude_test_brand_q,
    build_request_brand_filter_q,
    build_seller_brand_filter_q,
)
from marketing.services.simple_mailing.constants import (
    MARKETPLACE_BRAND_FILTER_AVAILABLE,
    RECIPIENT_TYPE_MARKETPLACE_BUYERS,
    RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
    RECIPIENT_TYPE_SELLERS,
)


@dataclass(frozen=True)
class SimpleMailingLaunchRecipient:
    phone_normalized: str
    display_name: str
    city: str
    brands_label: str
    is_test_contact: bool


def _parts_request_buyer_recipients(
    *,
    all_brands: bool,
    brands: list[str],
) -> list[SimpleMailingLaunchRecipient]:
    qs = Request.objects.filter(
        buyer_contact__isnull=False,
        buyer_contact__is_test_contact=False,
    ).exclude(build_exclude_test_brand_q())
    if not all_brands:
        qs = qs.filter(build_request_brand_filter_q(brands))

    buyer_ids = list(
        qs.order_by('buyer_contact_id')
        .values_list('buyer_contact_id', flat=True)
        .distinct()
    )
    buyers = {
        buyer.pk: buyer
        for buyer in BuyerContact.objects.filter(pk__in=buyer_ids)
    }
    recipients: list[SimpleMailingLaunchRecipient] = []
    for buyer_id in buyer_ids:
        buyer = buyers.get(buyer_id)
        if buyer is None or not buyer.phone_normalized:
            continue
        brand_names = _sorted_unique_brands(
            qs.filter(buyer_contact_id=buyer_id).values_list('brand', flat=True),
        )
        recipients.append(
            SimpleMailingLaunchRecipient(
                phone_normalized=buyer.phone_normalized,
                display_name='—',
                city=buyer.primary_city or '—',
                brands_label=', '.join(brand_names) or '—',
                is_test_contact=buyer.is_test_contact,
            ),
        )
    return _dedupe_by_phone(recipients)


def _marketplace_buyer_recipients() -> list[SimpleMailingLaunchRecipient]:
    counts = get_marketplace_buyer_counts()
    recipients: list[SimpleMailingLaunchRecipient] = []
    for phone_key in sorted(counts.real_phones):
        buyer = BuyerContact.objects.filter(phone_normalized=phone_key).first()
        recipients.append(
            SimpleMailingLaunchRecipient(
                phone_normalized=phone_key,
                display_name='—',
                city=(buyer.primary_city if buyer else '') or '—',
                brands_label='Все марки',
                is_test_contact=bool(buyer and buyer.is_test_contact),
            ),
        )
    return recipients


def _seller_recipients(
    *,
    all_brands: bool,
    brands: list[str],
) -> list[SimpleMailingLaunchRecipient]:
    qs = Seller.objects.filter(
        is_active=True,
        is_test_seller=False,
        is_paused=False,
    ).select_related('brand_fk').prefetch_related('selected_brands')
    if not all_brands:
        qs = qs.filter(build_seller_brand_filter_q(brands)).distinct()

    grouped: dict[str, list[Seller]] = {}
    for seller in qs.order_by('id'):
        phone_key = normalize_phone_key(seller.whatsapp)
        if not phone_key:
            continue
        grouped.setdefault(phone_key, []).append(seller)

    recipients: list[SimpleMailingLaunchRecipient] = []
    for phone_key in sorted(grouped.keys()):
        sellers = grouped[phone_key]
        recipients.append(
            SimpleMailingLaunchRecipient(
                phone_normalized=phone_key,
                display_name=sellers[0].name or '—',
                city=sellers[0].city or '—',
                brands_label=_merged_seller_brands_label(sellers),
                is_test_contact=False,
            ),
        )
    return recipients


def _merged_seller_brands_label(sellers: list[Seller]) -> str:
    if any(seller.all_brands for seller in sellers):
        return 'Все марки'
    names: set[str] = set()
    for seller in sellers:
        if seller.brand:
            names.add(seller.brand.strip())
        if seller.brand_fk_id and seller.brand_fk:
            names.add(seller.brand_fk.name)
        for brand in seller.selected_brands.all():
            if brand.name:
                names.add(brand.name)
    return ', '.join(sorted(names, key=lambda item: item.casefold())) or '—'


def _sorted_unique_brands(values) -> list[str]:
    seen: set[str] = set()
    brands: list[str] = []
    for value in values:
        text = str(value or '').strip()
        if not text:
            continue
        key = normalize_buyer_text(text)
        if not key or key in seen:
            continue
        seen.add(key)
        brands.append(text)
    return sorted(brands, key=lambda item: item.casefold())


def _dedupe_by_phone(
    recipients: list[SimpleMailingLaunchRecipient],
) -> list[SimpleMailingLaunchRecipient]:
    seen: set[str] = set()
    deduped: list[SimpleMailingLaunchRecipient] = []
    for recipient in sorted(recipients, key=lambda item: item.phone_normalized):
        if recipient.phone_normalized in seen:
            continue
        seen.add(recipient.phone_normalized)
        deduped.append(recipient)
    return deduped


def resolve_simple_mailing_launch_recipients(
    *,
    recipient_type: str,
    all_brands: bool = False,
    brands: list[str] | None = None,
) -> list[SimpleMailingLaunchRecipient]:
    brand_list = list(brands or [])
    if recipient_type == RECIPIENT_TYPE_PARTS_REQUEST_BUYERS:
        return _parts_request_buyer_recipients(all_brands=all_brands, brands=brand_list)
    if recipient_type == RECIPIENT_TYPE_MARKETPLACE_BUYERS:
        if not all_brands and MARKETPLACE_BRAND_FILTER_AVAILABLE:
            raise NotImplementedError('Marketplace brand filter is not enabled yet.')
        return _marketplace_buyer_recipients()
    if recipient_type == RECIPIENT_TYPE_SELLERS:
        return _seller_recipients(all_brands=all_brands, brands=brand_list)
    raise ValueError(f'Unknown recipient type: {recipient_type}')
