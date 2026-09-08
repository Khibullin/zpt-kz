from __future__ import annotations

from dataclasses import dataclass

from core.models import BuyerContact, Request
from core.phone_utils import normalize_kz_phone
from core.services.buyer_contact_utils import normalize_buyer_text
from marketing.services.marketplace_orders import get_marketplace_buyer_counts
from marketing.services.phone_utils import normalize_phone_key
from marketing.services.simple_mailing.brands import (
    build_exclude_test_brand_q,
    build_request_brand_filter_q,
)
from marketing.services.simple_mailing.constants import (
    MARKETPLACE_BRAND_FILTER_AVAILABLE,
    RECIPIENT_TYPE_MARKETPLACE_BUYERS,
    RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
    RECIPIENT_TYPE_SELLERS,
)
from marketing.services.simple_mailing.seller_selectability import (
    list_selectable_sellers,
    seller_brands_label,
)


@dataclass(frozen=True)
class SimpleMailingLaunchRecipient:
    phone_normalized: str
    display_name: str
    city: str
    brands_label: str
    is_test_contact: bool
    is_control_recipient: bool = False


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
                is_control_recipient=buyer.is_control_recipient,
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
                is_control_recipient=bool(buyer and buyer.is_control_recipient),
            ),
        )
    return recipients


def _seller_recipients(
    *,
    all_brands: bool,
    brands: list[str],
    selected_seller_ids: list[int] | None = None,
) -> list[SimpleMailingLaunchRecipient]:
    sellers = list_selectable_sellers(
        all_brands=all_brands,
        brands=brands,
        selected_seller_ids=selected_seller_ids,
    )
    recipients: list[SimpleMailingLaunchRecipient] = []
    for seller in sellers:
        phone_key = normalize_kz_phone(seller.whatsapp)
        if not phone_key:
            continue
        recipients.append(
            SimpleMailingLaunchRecipient(
                phone_normalized=phone_key,
                display_name=seller.name or '—',
                city=seller.city or '—',
                brands_label=seller_brands_label(seller),
                is_test_contact=False,
                is_control_recipient=False,
            ),
        )
    return recipients


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
    recipient_scope: str,
    all_brands: bool = False,
    brands: list[str] | None = None,
    selected_seller_ids: list[int] | None = None,
) -> list[SimpleMailingLaunchRecipient]:
    from marketing.services.simple_mailing.constants import (
        RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
        RECIPIENT_SCOPE_CONTROL_ONLY,
    )
    from marketing.services.simple_mailing.control_recipients import (
        build_control_launch_recipients,
        merge_ordinary_with_controls,
    )

    brand_list = list(brands or [])
    if recipient_scope == RECIPIENT_SCOPE_CONTROL_ONLY:
        return build_control_launch_recipients()

    if recipient_type == RECIPIENT_TYPE_PARTS_REQUEST_BUYERS:
        ordinary = _parts_request_buyer_recipients(all_brands=all_brands, brands=brand_list)
    elif recipient_type == RECIPIENT_TYPE_MARKETPLACE_BUYERS:
        if not all_brands and MARKETPLACE_BRAND_FILTER_AVAILABLE:
            raise NotImplementedError('Marketplace brand filter is not enabled yet.')
        ordinary = _marketplace_buyer_recipients()
    elif recipient_type == RECIPIENT_TYPE_SELLERS:
        ordinary = _seller_recipients(
            all_brands=all_brands,
            brands=brand_list,
            selected_seller_ids=selected_seller_ids,
        )
    else:
        raise ValueError(f'Unknown recipient type: {recipient_type}')

    if recipient_scope == RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS:
        controls = build_control_launch_recipients()
        merged, _ = merge_ordinary_with_controls(ordinary, controls)
        return merged
    return ordinary
