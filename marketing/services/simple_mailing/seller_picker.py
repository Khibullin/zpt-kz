from __future__ import annotations

from dataclasses import dataclass

from core.models import (
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    Seller,
    SellerContactConsent,
)
from core.phone_utils import normalize_kz_phone
from core.services.seller_whatsapp_consent import (
    SellerWhatsAppConsentError,
    build_seller_whatsapp_consent_url,
)
from marketing.services.simple_mailing.brands import (
    SimpleMailingValidationError,
    build_seller_brand_filter_q,
)
from marketing.services.simple_mailing.constants import (
    SELLER_CONSENT_FILTER_ALL,
    SELLER_CONSENT_FILTER_GRANTED,
    SELLER_CONSENT_FILTER_NOT_RECORDED,
    SELLER_CONSENT_FILTER_REVOKED,
    SELLER_CONSENT_FILTER_VALUES,
    SELLER_SELECT_FIRST_N,
)

CONSENT_LABEL_GRANTED = 'Подтверждено'
CONSENT_LABEL_REVOKED = 'Отключено'
CONSENT_LABEL_NOT_RECORDED = 'Не подтверждено'


@dataclass(frozen=True)
class SellerPickerRow:
    seller_id: int
    recipient_key: str
    name: str
    whatsapp: str
    city: str
    brands_label: str
    consent_status: str
    consent_label: str
    consent_badge: str
    receive_requests: bool
    is_paused: bool
    consent_url: str
    marketing_send_eligible: bool
    selected: bool = False


@dataclass(frozen=True)
class SellerSelectionSummary:
    selected_count: int
    whatsapp_allowed_count: int
    not_recorded_count: int
    revoked_count: int


def seller_recipient_key(seller_id: int) -> str:
    return f'seller:{int(seller_id)}'


def parse_selected_seller_ids(raw_values) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for value in raw_values or []:
        text = str(value or '').strip()
        if text.startswith('seller:'):
            text = text.split(':', 1)[1].strip()
        try:
            seller_id = int(text)
        except (TypeError, ValueError):
            continue
        if seller_id <= 0 or seller_id in seen:
            continue
        seen.add(seller_id)
        ids.append(seller_id)
    return ids


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


def consent_display(status: str) -> tuple[str, str, str]:
    if status == CONTACT_CONSENT_STATUS_GRANTED:
        return status, CONSENT_LABEL_GRANTED, 'granted'
    if status == CONTACT_CONSENT_STATUS_REVOKED:
        return status, CONSENT_LABEL_REVOKED, 'revoked'
    return status or '', CONSENT_LABEL_NOT_RECORDED, 'unknown'


def seller_is_marketing_send_eligible(seller: Seller, consent_status: str) -> bool:
    return (
        consent_status == CONTACT_CONSENT_STATUS_GRANTED
        and bool(seller.is_active)
        and not seller.is_test_seller
        and bool(seller.receive_requests)
        and not seller.is_paused
    )


def _consent_status_for_seller(
    seller: Seller,
    consent_by_key: dict[tuple[int, str], str],
) -> str:
    phone = normalize_kz_phone(seller.whatsapp)
    if not phone:
        return ''
    return consent_by_key.get((seller.pk, phone), '')


def _consent_url_for_seller(seller: Seller, consent_status: str) -> str:
    if consent_status in {CONTACT_CONSENT_STATUS_GRANTED, CONTACT_CONSENT_STATUS_REVOKED}:
        return ''
    try:
        return build_seller_whatsapp_consent_url(seller)
    except SellerWhatsAppConsentError:
        return ''


def _load_consent_map(sellers: list[Seller]) -> dict[tuple[int, str], str]:
    if not sellers:
        return {}
    consent_by_key: dict[tuple[int, str], str] = {}
    rows = (
        SellerContactConsent.objects.filter(
            seller_id__in=[seller.pk for seller in sellers],
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        )
        .order_by('seller_id', '-updated_at', '-id')
        .values_list('seller_id', 'phone_normalized', 'status')
    )
    for seller_id, phone, status in rows:
        key = (seller_id, phone)
        if key not in consent_by_key:
            consent_by_key[key] = status
    return consent_by_key


def _row_from_seller(
    seller: Seller,
    *,
    consent_by_key: dict[tuple[int, str], str],
    selected_ids: set[int],
) -> SellerPickerRow:
    raw_status = _consent_status_for_seller(seller, consent_by_key)
    status, label, badge = consent_display(raw_status)
    return SellerPickerRow(
        seller_id=seller.pk,
        recipient_key=seller_recipient_key(seller.pk),
        name=seller.name or '—',
        whatsapp=str(seller.whatsapp or ''),
        city=seller.city or '—',
        brands_label=seller_brands_label(seller),
        consent_status=status,
        consent_label=label,
        consent_badge=badge,
        receive_requests=bool(seller.receive_requests),
        is_paused=bool(seller.is_paused),
        consent_url=_consent_url_for_seller(seller, status),
        marketing_send_eligible=seller_is_marketing_send_eligible(seller, status),
        selected=seller.pk in selected_ids,
    )


def normalize_consent_filter(value: object) -> str:
    text = str(value or '').strip()
    if text in SELLER_CONSENT_FILTER_VALUES:
        return text
    return SELLER_CONSENT_FILTER_ALL


def _row_matches_consent_filter(row: SellerPickerRow, consent_filter: str) -> bool:
    if consent_filter == SELLER_CONSENT_FILTER_ALL:
        return True
    if consent_filter == SELLER_CONSENT_FILTER_GRANTED:
        return row.consent_status == CONTACT_CONSENT_STATUS_GRANTED
    if consent_filter == SELLER_CONSENT_FILTER_REVOKED:
        return row.consent_status == CONTACT_CONSENT_STATUS_REVOKED
    if consent_filter == SELLER_CONSENT_FILTER_NOT_RECORDED:
        return row.consent_status not in {
            CONTACT_CONSENT_STATUS_GRANTED,
            CONTACT_CONSENT_STATUS_REVOKED,
        }
    return True


def _row_matches_search(row: SellerPickerRow, query: str) -> bool:
    if not query:
        return True
    needle = query.casefold()
    return needle in row.name.casefold() or needle in row.whatsapp.casefold()


def list_seller_picker_rows(
    *,
    all_brands: bool,
    brands: list[str] | None = None,
    consent_filter: str = SELLER_CONSENT_FILTER_ALL,
    search: str = '',
    selected_seller_ids: list[int] | None = None,
) -> tuple[SellerPickerRow, ...]:
    qs = (
        seller_audience_queryset(all_brands=all_brands, brands=brands)
        .select_related('brand_fk')
        .prefetch_related('selected_brands')
        .order_by('id')
    )
    sellers = list(qs)
    consent_by_key = _load_consent_map(sellers)
    selected_ids = set(selected_seller_ids or [])
    consent_filter = normalize_consent_filter(consent_filter)
    query = str(search or '').strip()
    rows: list[SellerPickerRow] = []
    for seller in sellers:
        row = _row_from_seller(
            seller,
            consent_by_key=consent_by_key,
            selected_ids=selected_ids,
        )
        if not _row_matches_consent_filter(row, consent_filter):
            continue
        if not _row_matches_search(row, query):
            continue
        rows.append(row)
    return tuple(rows)


def first_n_seller_ids(
    rows: tuple[SellerPickerRow, ...] | list[SellerPickerRow],
    n: int = SELLER_SELECT_FIRST_N,
) -> list[int]:
    limit = max(int(n), 0)
    return [row.seller_id for row in list(rows)[:limit]]


def validate_selected_seller_ids(
    selected_ids: list[int],
    *,
    all_brands: bool,
    brands: list[str] | None = None,
) -> list[int]:
    allowed = {
        row.seller_id
        for row in list_seller_picker_rows(
            all_brands=all_brands,
            brands=brands,
        )
    }
    validated: list[int] = []
    seen: set[int] = set()
    for seller_id in selected_ids:
        if seller_id not in allowed:
            raise SimpleMailingValidationError(
                'Выбранные продавцы не входят в текущую аудиторию.'
            )
        if seller_id in seen:
            continue
        seen.add(seller_id)
        validated.append(seller_id)
    return validated


def build_seller_selection_summary(
    rows: tuple[SellerPickerRow, ...] | list[SellerPickerRow],
    selected_ids: list[int] | None = None,
) -> SellerSelectionSummary:
    selected = set(selected_ids or [])
    chosen = [row for row in rows if row.seller_id in selected] if selected else list(rows)
    return SellerSelectionSummary(
        selected_count=len(chosen),
        whatsapp_allowed_count=sum(1 for row in chosen if row.marketing_send_eligible),
        not_recorded_count=sum(
            1
            for row in chosen
            if row.consent_status not in {
                CONTACT_CONSENT_STATUS_GRANTED,
                CONTACT_CONSENT_STATUS_REVOKED,
            }
        ),
        revoked_count=sum(
            1 for row in chosen if row.consent_status == CONTACT_CONSENT_STATUS_REVOKED
        ),
    )


def seller_summary_rows_for_ids(
    selected_ids: list[int],
    *,
    all_brands: bool,
    brands: list[str] | None = None,
) -> tuple[SellerPickerRow, ...]:
    if not selected_ids:
        return ()
    validated = validate_selected_seller_ids(
        selected_ids,
        all_brands=all_brands,
        brands=brands,
    )
    by_id = {
        row.seller_id: row
        for row in list_seller_picker_rows(
            all_brands=all_brands,
            brands=brands,
            selected_seller_ids=validated,
        )
    }
    return tuple(by_id[seller_id] for seller_id in validated if seller_id in by_id)
