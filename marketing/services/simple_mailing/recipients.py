from __future__ import annotations

from dataclasses import dataclass

from core.models import BuyerContact, Request, Seller
from core.services.buyer_contact_utils import mask_phone, normalize_buyer_text
from marketing.services.marketplace_orders import get_marketplace_buyer_counts
from marketing.services.phone_utils import normalize_phone_key
from marketing.services.simple_mailing.brands import (
    build_request_brand_filter_q,
    build_seller_brand_filter_q,
)
from marketing.services.simple_mailing.constants import (
    MARKETPLACE_BRAND_FILTER_AVAILABLE,
    RECIPIENT_TYPE_MARKETPLACE_BUYERS,
    RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
    RECIPIENT_TYPE_SELLERS,
)

PREVIEW_LIMIT = 50

RECIPIENT_LABELS = {
    RECIPIENT_TYPE_PARTS_REQUEST_BUYERS: 'Покупатель',
    RECIPIENT_TYPE_MARKETPLACE_BUYERS: 'Покупатель Marketplace',
    RECIPIENT_TYPE_SELLERS: 'Продавец',
}


@dataclass(frozen=True)
class SimpleMailingPreviewRow:
    masked_phone: str
    recipient_type_label: str
    brands_label: str
    recipient_key: str


@dataclass(frozen=True)
class SimpleMailingSelection:
    recipient_type: str
    all_brands: bool
    brands: tuple[str, ...]


@dataclass(frozen=True)
class SimpleMailingRecipientsResult:
    selection: SimpleMailingSelection
    count: int
    recipient_keys: tuple[str, ...]
    preview_rows: tuple[SimpleMailingPreviewRow, ...]


def _parts_request_buyers(
    *,
    all_brands: bool,
    brands: list[str],
) -> SimpleMailingRecipientsResult:
    selection = SimpleMailingSelection(
        recipient_type=RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
        all_brands=all_brands,
        brands=tuple(brands),
    )
    qs = Request.objects.filter(
        buyer_contact__isnull=False,
        buyer_contact__is_test_contact=False,
    )
    if not all_brands:
        qs = qs.filter(build_request_brand_filter_q(brands))

    buyer_ids = list(
        qs.order_by('buyer_contact_id')
        .values_list('buyer_contact_id', flat=True)
        .distinct()[:PREVIEW_LIMIT]
    )
    count = qs.values('buyer_contact_id').distinct().count()
    preview_buyer_ids = buyer_ids
    buyers = {
        buyer.pk: buyer
        for buyer in BuyerContact.objects.filter(pk__in=preview_buyer_ids)
    }
    preview_rows: list[SimpleMailingPreviewRow] = []
    for buyer_id in preview_buyer_ids:
        buyer = buyers.get(buyer_id)
        if buyer is None:
            continue
        buyer_brands = _sorted_unique_brands(
            qs.filter(buyer_contact_id=buyer_id).values_list('brand', flat=True),
        )
        preview_rows.append(
            SimpleMailingPreviewRow(
                masked_phone=mask_phone(buyer.phone_normalized),
                recipient_type_label=RECIPIENT_LABELS[RECIPIENT_TYPE_PARTS_REQUEST_BUYERS],
                brands_label=', '.join(buyer_brands) or '—',
                recipient_key=f'buyer:{buyer_id}',
            ),
        )
    recipient_keys = ()  # populated only when needed for draft; count is authoritative
    return SimpleMailingRecipientsResult(
        selection=selection,
        count=count,
        recipient_keys=recipient_keys,
        preview_rows=tuple(preview_rows),
    )


def _marketplace_buyers(
    *,
    all_brands: bool,
    brands: list[str],
) -> SimpleMailingRecipientsResult:
    selection = SimpleMailingSelection(
        recipient_type=RECIPIENT_TYPE_MARKETPLACE_BUYERS,
        all_brands=all_brands,
        brands=tuple(brands),
    )
    if not all_brands and MARKETPLACE_BRAND_FILTER_AVAILABLE:
        raise NotImplementedError('Marketplace brand filter is not enabled yet.')

    counts = get_marketplace_buyer_counts()
    phone_keys = sorted(counts.real_phones)
    preview_rows = [
        SimpleMailingPreviewRow(
            masked_phone=mask_phone(phone_key),
            recipient_type_label=RECIPIENT_LABELS[RECIPIENT_TYPE_MARKETPLACE_BUYERS],
            brands_label='Все марки',
            recipient_key=f'phone:{phone_key}',
        )
        for phone_key in phone_keys[:PREVIEW_LIMIT]
    ]
    return SimpleMailingRecipientsResult(
        selection=selection,
        count=counts.real_total,
        recipient_keys=(),
        preview_rows=tuple(preview_rows),
    )


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


def _group_sellers_by_phone(qs) -> dict[str, list[Seller]]:
    grouped: dict[str, list[Seller]] = {}
    for seller in qs.order_by('id'):
        phone_key = normalize_phone_key(seller.whatsapp)
        if not phone_key:
            continue
        grouped.setdefault(phone_key, []).append(seller)
    return grouped


def _seller_preview_rows(grouped: dict[str, list[Seller]]) -> tuple[SimpleMailingPreviewRow, ...]:
    preview_rows: list[SimpleMailingPreviewRow] = []
    for phone_key in sorted(grouped.keys())[:PREVIEW_LIMIT]:
        sellers = grouped[phone_key]
        preview_rows.append(
            SimpleMailingPreviewRow(
                masked_phone=mask_phone(phone_key),
                recipient_type_label=RECIPIENT_LABELS[RECIPIENT_TYPE_SELLERS],
                brands_label=_merged_seller_brands_label(sellers),
                recipient_key=f'phone:{phone_key}',
            ),
        )
    return tuple(preview_rows)


def _sellers(
    *,
    all_brands: bool,
    brands: list[str],
) -> SimpleMailingRecipientsResult:
    selection = SimpleMailingSelection(
        recipient_type=RECIPIENT_TYPE_SELLERS,
        all_brands=all_brands,
        brands=tuple(brands),
    )
    qs = Seller.objects.filter(
        is_active=True,
        is_test_seller=False,
        is_paused=False,
    ).select_related('brand_fk').prefetch_related('selected_brands')
    if not all_brands:
        qs = qs.filter(build_seller_brand_filter_q(brands)).distinct()

    grouped = _group_sellers_by_phone(qs)
    count = len(grouped)
    preview_rows = _seller_preview_rows(grouped)
    return SimpleMailingRecipientsResult(
        selection=selection,
        count=count,
        recipient_keys=(),
        preview_rows=preview_rows,
    )


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


def resolve_simple_mailing_recipients(
    *,
    recipient_type: str,
    all_brands: bool = False,
    brands: list[str] | None = None,
) -> SimpleMailingRecipientsResult:
    brand_list = list(brands or [])
    if recipient_type == RECIPIENT_TYPE_PARTS_REQUEST_BUYERS:
        return _parts_request_buyers(all_brands=all_brands, brands=brand_list)
    if recipient_type == RECIPIENT_TYPE_MARKETPLACE_BUYERS:
        return _marketplace_buyers(all_brands=all_brands, brands=brand_list)
    if recipient_type == RECIPIENT_TYPE_SELLERS:
        return _sellers(all_brands=all_brands, brands=brand_list)
    raise ValueError(f'Unknown recipient type: {recipient_type}')
