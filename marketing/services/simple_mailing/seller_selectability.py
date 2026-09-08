"""Shared Seller selectability for picker, preview counts, and launch recipients.

Phone identity matches core.services.seller_identity.find_sellers_by_phone:
a canonical WhatsApp is unique only if that lookup returns exactly one Seller,
checked globally (not limited to the current brand filter).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from core.models import Seller
from core.phone_utils import normalize_kz_phone
from core.services.seller_identity import phone_lookup_variants
from marketing.services.simple_mailing.brands import build_seller_brand_filter_q

WHATSAPP_STATE_READY = 'ready'
WHATSAPP_STATE_INVALID = 'invalid'
WHATSAPP_STATE_AMBIGUOUS = 'ambiguous'

WHATSAPP_STATUS_LABEL_READY = 'Готов'
WHATSAPP_STATUS_LABEL_INVALID = 'Неверный номер'
WHATSAPP_STATUS_LABEL_AMBIGUOUS = 'Номер используется несколькими продавцами'

WHATSAPP_STATUS_LABELS = {
    WHATSAPP_STATE_READY: WHATSAPP_STATUS_LABEL_READY,
    WHATSAPP_STATE_INVALID: WHATSAPP_STATUS_LABEL_INVALID,
    WHATSAPP_STATE_AMBIGUOUS: WHATSAPP_STATUS_LABEL_AMBIGUOUS,
}


@dataclass(frozen=True)
class SellerPhoneIdentityIndex:
    """raw Seller.whatsapp value -> seller ids, matching find_sellers_by_phone SQL."""

    raw_to_ids: dict[str, tuple[int, ...]]

    def seller_ids_for_canonical(self, canonical: str) -> tuple[int, ...]:
        variants = phone_lookup_variants(canonical)
        ids: list[int] = []
        seen: set[int] = set()
        for variant in variants:
            for seller_id in self.raw_to_ids.get(variant, ()):
                if seller_id in seen:
                    continue
                seen.add(seller_id)
                ids.append(seller_id)
        return tuple(ids)


@dataclass(frozen=True)
class SellerWhatsAppClassification:
    state: str
    canonical: str | None

    @property
    def label(self) -> str:
        return WHATSAPP_STATUS_LABELS[self.state]

    @property
    def is_ready(self) -> bool:
        return self.state == WHATSAPP_STATE_READY


@dataclass(frozen=True)
class SellerAudienceCounts:
    found_count: int = 0
    selectable_count: int = 0
    invalid_whatsapp_count: int = 0
    ambiguous_whatsapp_count: int = 0


def build_seller_phone_identity_index() -> SellerPhoneIdentityIndex:
    raw_to_ids: dict[str, list[int]] = defaultdict(list)
    for seller_id, raw in Seller.objects.values_list('id', 'whatsapp'):
        raw_to_ids[str(raw or '')].append(seller_id)
    return SellerPhoneIdentityIndex(
        raw_to_ids={key: tuple(ids) for key, ids in raw_to_ids.items()},
    )


def classify_seller_whatsapp(
    seller: Seller,
    index: SellerPhoneIdentityIndex,
) -> SellerWhatsAppClassification:
    canonical = normalize_kz_phone(seller.whatsapp)
    if not canonical:
        return SellerWhatsAppClassification(WHATSAPP_STATE_INVALID, None)
    matches = index.seller_ids_for_canonical(canonical)
    if not matches:
        return SellerWhatsAppClassification(WHATSAPP_STATE_INVALID, canonical)
    if len(matches) != 1 or matches[0] != seller.pk:
        return SellerWhatsAppClassification(WHATSAPP_STATE_AMBIGUOUS, canonical)
    return SellerWhatsAppClassification(WHATSAPP_STATE_READY, canonical)


def seller_matches_operational_flags(seller: Seller) -> bool:
    return (
        bool(seller.is_active)
        and not seller.is_test_seller
        and bool(seller.receive_requests)
        and not seller.is_paused
    )


def seller_is_technically_selectable(
    seller: Seller,
    index: SellerPhoneIdentityIndex,
) -> bool:
    if not seller_matches_operational_flags(seller):
        return False
    return classify_seller_whatsapp(seller, index).is_ready


def operational_seller_queryset():
    return Seller.objects.filter(
        is_active=True,
        is_test_seller=False,
        is_paused=False,
        receive_requests=True,
    )


def apply_seller_brand_filter(qs, *, all_brands: bool, brands: list[str] | None):
    if all_brands:
        return qs
    return qs.filter(build_seller_brand_filter_q(list(brands or []))).distinct()


def seller_audience_queryset(*, all_brands: bool, brands: list[str] | None):
    return apply_seller_brand_filter(
        operational_seller_queryset(),
        all_brands=all_brands,
        brands=brands,
    )


def list_audience_sellers(*, all_brands: bool, brands: list[str] | None) -> list[Seller]:
    return list(
        seller_audience_queryset(all_brands=all_brands, brands=brands)
        .select_related('brand_fk')
        .prefetch_related('selected_brands')
        .order_by('id')
    )


def summarize_seller_audience(
    sellers: list[Seller],
    index: SellerPhoneIdentityIndex,
) -> SellerAudienceCounts:
    invalid = 0
    ambiguous = 0
    selectable = 0
    for seller in sellers:
        state = classify_seller_whatsapp(seller, index).state
        if state == WHATSAPP_STATE_READY:
            selectable += 1
        elif state == WHATSAPP_STATE_INVALID:
            invalid += 1
        else:
            ambiguous += 1
    return SellerAudienceCounts(
        found_count=len(sellers),
        selectable_count=selectable,
        invalid_whatsapp_count=invalid,
        ambiguous_whatsapp_count=ambiguous,
    )


def list_selectable_sellers(
    *,
    all_brands: bool,
    brands: list[str] | None = None,
    selected_seller_ids: list[int] | None = None,
    index: SellerPhoneIdentityIndex | None = None,
) -> list[Seller]:
    """Return technically selectable Sellers for preview or exact launch.

    When selected_seller_ids is set, keep that exact id order and drop any id
    that is no longer selectable. Never substitute another Seller.
    """
    identity = index or build_seller_phone_identity_index()
    audience = list_audience_sellers(all_brands=all_brands, brands=brands)
    selectable = {
        seller.pk: seller
        for seller in audience
        if seller_is_technically_selectable(seller, identity)
    }
    if selected_seller_ids is None:
        return [seller for seller in audience if seller.pk in selectable]
    return [
        selectable[seller_id]
        for seller_id in selected_seller_ids
        if seller_id in selectable
    ]


def seller_brands_label(seller: Seller) -> str:
    if seller.all_brands:
        return 'Все марки'
    names: set[str] = set()
    brand = str(getattr(seller, 'brand', '') or '').strip()
    if brand:
        names.add(brand)
    brand_fk = getattr(seller, 'brand_fk', None)
    if brand_fk is not None and brand_fk.name:
        names.add(brand_fk.name)
    for selected in seller.selected_brands.all():
        if selected.name:
            names.add(selected.name)
    return ', '.join(sorted(names, key=lambda item: item.casefold())) or '—'
