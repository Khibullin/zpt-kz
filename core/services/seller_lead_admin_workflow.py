from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from django.db import transaction
from django.utils import timezone

from core.models import Brand, Country, Seller, SellerLead, SellerLeadContactCandidate, TRANSPORT_CHOICES


REQUEST_SELLER_TRANSPORT_TYPES = {choice[0] for choice in TRANSPORT_CHOICES}


class WorkflowResultKind(str, Enum):
    SUCCESS = 'success'
    WARNING = 'warning'
    ERROR = 'error'


@dataclass(frozen=True)
class WorkflowActionResult:
    kind: WorkflowResultKind
    message: str
    lead_id: int
    seller_id: int | None = None
    created_seller: bool = False
    linked_existing_seller: bool = False


@dataclass(frozen=True)
class RequestSellerActivationProfile:
    transport_type: str
    all_categories: bool = True
    all_countries: bool = True
    all_brands: bool = True
    all_models: bool = True
    country_names: tuple[str, ...] = ()
    brand_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class RequestSellerActivationResult:
    lead_id: int
    seller_id: int
    created_seller: bool
    receive_requests: bool


def normalize_request_seller_whatsapp(value: str | None) -> str:
    digits = ''.join(char for char in str(value or '') if char.isdigit())
    if digits.startswith('8') and len(digits) == 11:
        digits = '7' + digits[1:]
    return digits


def find_request_seller_by_whatsapp(whatsapp: str | None) -> Seller | None:
    target = normalize_request_seller_whatsapp(whatsapp)
    if not target:
        return None

    for seller in Seller.objects.all():
        if normalize_request_seller_whatsapp(seller.whatsapp) == target:
            return seller

    return None


def has_unresolved_contact_conflict(lead: SellerLead) -> bool:
    if lead.whatsapp:
        return False
    return lead.contact_candidates.filter(
        status=SellerLeadContactCandidate.STATUS_CONFLICT,
    ).exists()


def build_request_seller_notes(lead: SellerLead) -> str:
    parts: list[str] = []
    if lead.instagram_username:
        parts.append(f'Instagram: @{lead.instagram_username}')
    elif lead.instagram_url:
        parts.append(f'Instagram: {lead.instagram_url}')
    if lead.source_url:
        parts.append(f'Источник: {lead.source_url}')
    parts.append(f'SellerLead #{lead.pk}')
    return '\n'.join(parts)


def compute_review_status(lead: SellerLead) -> str:
    has_seller = lead.request_seller_id is not None
    has_marketplace = (
        lead.marketplace_invitation_status == SellerLead.MARKETPLACE_INVITATION_PLANNED
    )

    if has_seller and has_marketplace:
        return SellerLead.REVIEW_CONVERTED_AND_MARKETPLACE_PLANNED
    if has_seller:
        return SellerLead.REVIEW_CONVERTED_REQUESTS
    if has_marketplace:
        return SellerLead.REVIEW_MARKETPLACE_PLANNED
    return SellerLead.REVIEW_NEEDS_REVIEW


def _lead_label(lead: SellerLead) -> str:
    if lead.instagram_username:
        return f'@{lead.instagram_username}'
    return lead.name


def get_lead_request_seller_transport_type(lead: SellerLead) -> str | None:
    value = (lead.request_seller_transport_type or '').strip()
    if value in REQUEST_SELLER_TRANSPORT_TYPES:
        return value
    return None


def _save_lead_workflow_fields(lead: SellerLead, *, update_fields: list[str]) -> None:
    lead.review_status = compute_review_status(lead)
    fields = set(update_fields)
    fields.add('review_status')
    fields.add('updated_at')
    lead.save(update_fields=sorted(fields))


def _enable_request_delivery(seller: Seller) -> None:
    update_fields: list[str] = []
    if not seller.is_active:
        seller.is_active = True
        update_fields.append('is_active')
    if seller.is_paused:
        seller.is_paused = False
        update_fields.append('is_paused')
    if not seller.receive_requests:
        seller.receive_requests = True
        update_fields.append('receive_requests')
    if update_fields:
        seller.save(update_fields=update_fields)


def _activate_linked_lead(
    lead: SellerLead,
    seller: Seller,
    *,
    now,
    update_fields: list[str] | None = None,
) -> None:
    _enable_request_delivery(seller)
    fields = list(update_fields or [])
    if lead.request_seller_id != seller.pk:
        lead.request_seller = seller
        fields.append('request_seller')
    if lead.request_seller_transport_type != seller.transport_type:
        lead.request_seller_transport_type = seller.transport_type
        fields.append('request_seller_transport_type')
    if lead.reviewed_at is None:
        lead.reviewed_at = now
        fields.append('reviewed_at')
    if lead.lifecycle_status != SellerLead.LIFECYCLE_ACTIVE:
        lead.lifecycle_status = SellerLead.LIFECYCLE_ACTIVE
        fields.append('lifecycle_status')
    _save_lead_workflow_fields(lead, update_fields=fields)


def convert_lead_to_request_seller(lead: SellerLead) -> WorkflowActionResult:
    """Link/create a request Seller and enable buyer-request delivery immediately.

    This is the default Seller Discovery conversion path. It does not send an
    invitation, create a marketplace account, or grant marketing consent.
    """
    label = _lead_label(lead)

    if has_unresolved_contact_conflict(lead):
        return WorkflowActionResult(
            kind=WorkflowResultKind.ERROR,
            message=(
                f'{label}: невозможно подключить — '
                'не выбран основной WhatsApp: требуется разрешить конфликт контактов'
            ),
            lead_id=lead.pk,
        )

    whatsapp = normalize_request_seller_whatsapp(lead.whatsapp)
    if not whatsapp:
        return WorkflowActionResult(
            kind=WorkflowResultKind.WARNING,
            message=f'{label}: невозможно подключить — WhatsApp отсутствует',
            lead_id=lead.pk,
        )

    if not lead.name.strip():
        return WorkflowActionResult(
            kind=WorkflowResultKind.ERROR,
            message=f'{label}: невозможно подключить — отсутствует название',
            lead_id=lead.pk,
        )

    existing_seller = find_request_seller_by_whatsapp(whatsapp)
    now = timezone.now()

    if lead.request_seller_id:
        linked = Seller.objects.filter(pk=lead.request_seller_id).first()
        if linked is None:
            return WorkflowActionResult(
                kind=WorkflowResultKind.ERROR,
                message=f'{label}: связанный продавец не найден',
                lead_id=lead.pk,
            )
        if existing_seller is not None and existing_seller.pk != linked.pk:
            linked = existing_seller
        _activate_linked_lead(lead, linked, now=now)
        return WorkflowActionResult(
            kind=WorkflowResultKind.WARNING,
            message=f'{label}: существующий продавец подключён к заявкам',
            lead_id=lead.pk,
            seller_id=linked.pk,
            linked_existing_seller=True,
        )

    if existing_seller:
        _activate_linked_lead(lead, existing_seller, now=now)
        return WorkflowActionResult(
            kind=WorkflowResultKind.WARNING,
            message=f'{label}: существующий продавец подключён к заявкам',
            lead_id=lead.pk,
            seller_id=existing_seller.pk,
            linked_existing_seller=True,
        )

    transport_type = get_lead_request_seller_transport_type(lead)
    if not transport_type:
        return WorkflowActionResult(
            kind=WorkflowResultKind.WARNING,
            message=(
                f'{label}: невозможно создать продавца — '
                'выберите тип транспорта (легковые или грузовые)'
            ),
            lead_id=lead.pk,
        )

    with transaction.atomic():
        seller = Seller.objects.create(
            name=lead.name[:255],
            whatsapp=whatsapp[:20],
            city=lead.city[:100],
            transport_type=transport_type,
            notes=build_request_seller_notes(lead),
            receive_requests=True,
            is_active=True,
            is_paused=False,
            all_categories=True,
            all_countries=True,
            all_brands=True,
            all_models=True,
        )
        lead.request_seller = seller
        lead.request_seller_transport_type = transport_type
        lead.reviewed_at = now
        lead.lifecycle_status = SellerLead.LIFECYCLE_ACTIVE
        _save_lead_workflow_fields(
            lead,
            update_fields=[
                'request_seller',
                'request_seller_transport_type',
                'reviewed_at',
                'lifecycle_status',
            ],
        )

    return WorkflowActionResult(
        kind=WorkflowResultKind.SUCCESS,
        message=f'{label} подключён к активным продавцам заявок',
        lead_id=lead.pk,
        seller_id=seller.pk,
        created_seller=True,
    )


def activate_invited_lead_for_requests(
    lead: SellerLead,
    profile: RequestSellerActivationProfile,
) -> RequestSellerActivationResult:
    """Create/link Seller and enable buyer-request delivery for a prepared lead.

    This does not register a marketplace user, does not grant marketing consent,
    and does not send any WhatsApp message. Direct request activation is valid
    for legacy invited leads and for the new ready-to-invite -> active flow.
    """
    if lead.lifecycle_status not in (
        SellerLead.LIFECYCLE_READY_TO_INVITE,
        SellerLead.LIFECYCLE_INVITED,
        SellerLead.LIFECYCLE_ACTIVE,
    ):
        raise ValueError('SellerLead must be prepared before request activation.')
    if profile.transport_type not in REQUEST_SELLER_TRANSPORT_TYPES:
        raise ValueError('Invalid request seller transport type.')

    whatsapp = normalize_request_seller_whatsapp(lead.whatsapp)
    if not whatsapp:
        raise ValueError('SellerLead has no valid WhatsApp.')

    with transaction.atomic():
        locked = SellerLead.objects.select_for_update().get(pk=lead.pk)
        existing = find_request_seller_by_whatsapp(whatsapp)

        if locked.request_seller_id:
            seller = Seller.objects.select_for_update().get(pk=locked.request_seller_id)
            if existing is not None and existing.pk != seller.pk:
                raise ValueError('WhatsApp belongs to another Seller.')
            created = False
        elif existing is not None:
            seller = Seller.objects.select_for_update().get(pk=existing.pk)
            locked.request_seller = seller
            created = False
        else:
            seller = Seller.objects.create(
                name=locked.name[:255],
                whatsapp=whatsapp[:20],
                city=locked.city[:100],
                transport_type=profile.transport_type,
                notes=build_request_seller_notes(locked),
                receive_requests=False,
                is_active=True,
                is_paused=False,
            )
            locked.request_seller = seller
            created = True

        seller.name = locked.name[:255] or seller.name
        seller.whatsapp = whatsapp[:20]
        seller.city = locked.city[:100]
        seller.transport_type = profile.transport_type
        seller.is_active = True
        seller.is_paused = False
        seller.receive_requests = True
        seller.all_categories = profile.all_categories
        seller.all_countries = profile.all_countries
        seller.all_brands = profile.all_brands
        seller.all_models = profile.all_models
        seller.category = ''
        seller.brand = ''
        seller.model = ''
        seller.country_fk = None
        seller.brand_fk = None
        seller.model_fk = None
        seller.save(update_fields=[
            'name',
            'whatsapp',
            'city',
            'transport_type',
            'is_active',
            'is_paused',
            'receive_requests',
            'all_categories',
            'all_countries',
            'all_brands',
            'all_models',
            'category',
            'brand',
            'model',
            'country_fk',
            'brand_fk',
            'model_fk',
        ])

        seller.selected_categories.clear()
        seller.selected_countries.clear()
        seller.selected_brands.clear()
        seller.selected_models.clear()

        if profile.country_names:
            countries = list(Country.objects.filter(name__in=profile.country_names))
            if len(countries) != len(set(profile.country_names)):
                found = {item.name for item in countries}
                missing = sorted(set(profile.country_names) - found)
                raise ValueError(f'Unknown countries: {", ".join(missing)}')
            seller.selected_countries.add(*countries)

        if profile.brand_names:
            brands = list(
                Brand.objects.filter(
                    name__in=profile.brand_names,
                    transport_type=profile.transport_type,
                )
            )
            if len(brands) != len(set(profile.brand_names)):
                found = {item.name for item in brands}
                missing = sorted(set(profile.brand_names) - found)
                raise ValueError(f'Unknown brands: {", ".join(missing)}')
            seller.selected_brands.add(*brands)

        locked.request_seller_transport_type = profile.transport_type
        locked.reviewed_at = locked.reviewed_at or timezone.now()
        locked.lifecycle_status = SellerLead.LIFECYCLE_ACTIVE
        _save_lead_workflow_fields(
            locked,
            update_fields=[
                'request_seller',
                'request_seller_transport_type',
                'reviewed_at',
                'lifecycle_status',
            ],
        )

    return RequestSellerActivationResult(
        lead_id=lead.pk,
        seller_id=seller.pk,
        created_seller=created,
        receive_requests=seller.receive_requests,
    )


def mark_marketplace_invitation_planned(lead: SellerLead) -> WorkflowActionResult:
    label = _lead_label(lead)
    now = timezone.now()

    if (
        lead.marketplace_invitation_status == SellerLead.MARKETPLACE_INVITATION_PLANNED
        and lead.review_status
        in (
            SellerLead.REVIEW_MARKETPLACE_PLANNED,
            SellerLead.REVIEW_CONVERTED_AND_MARKETPLACE_PLANNED,
        )
    ):
        return WorkflowActionResult(
            kind=WorkflowResultKind.WARNING,
            message=f'{label} уже отмечен для приглашения в маркетплейс',
            lead_id=lead.pk,
        )

    lead.marketplace_invitation_status = SellerLead.MARKETPLACE_INVITATION_PLANNED
    lead.marketplace_invitation_planned_at = lead.marketplace_invitation_planned_at or now
    lead.reviewed_at = lead.reviewed_at or now
    _save_lead_workflow_fields(
        lead,
        update_fields=[
            'marketplace_invitation_status',
            'marketplace_invitation_planned_at',
            'reviewed_at',
        ],
    )

    return WorkflowActionResult(
        kind=WorkflowResultKind.SUCCESS,
        message=f'{label} отмечен для приглашения в маркетплейс',
        lead_id=lead.pk,
    )


def convert_lead_and_mark_marketplace_planned(lead: SellerLead) -> WorkflowActionResult:
    convert_result = convert_lead_to_request_seller(lead)
    if convert_result.kind == WorkflowResultKind.ERROR:
        return convert_result
    if convert_result.kind == WorkflowResultKind.WARNING and not convert_result.seller_id:
        return convert_result

    marketplace_result = mark_marketplace_invitation_planned(lead)
    label = _lead_label(lead)

    if marketplace_result.kind == WorkflowResultKind.WARNING:
        return WorkflowActionResult(
            kind=WorkflowResultKind.WARNING,
            message=(
                f'{label}: добавлен в продавцы заявок; '
                'приглашение в маркетплейс уже было запланировано'
            ),
            lead_id=lead.pk,
            seller_id=convert_result.seller_id,
            created_seller=convert_result.created_seller,
            linked_existing_seller=convert_result.linked_existing_seller,
        )

    return WorkflowActionResult(
        kind=WorkflowResultKind.SUCCESS,
        message=(
            f'{label} добавлен в продавцы заявок и отмечен '
            'для приглашения в маркетплейс'
        ),
        lead_id=lead.pk,
        seller_id=convert_result.seller_id,
        created_seller=convert_result.created_seller,
        linked_existing_seller=convert_result.linked_existing_seller,
    )


def reject_lead(lead: SellerLead) -> WorkflowActionResult:
    label = _lead_label(lead)
    now = timezone.now()
    had_seller = lead.request_seller_id is not None

    if lead.review_status == SellerLead.REVIEW_REJECTED:
        message = f'{label} уже отклонён'
        if had_seller:
            message += '; рабочий продавец заявок сохранён'
        return WorkflowActionResult(
            kind=WorkflowResultKind.WARNING,
            message=message,
            lead_id=lead.pk,
            seller_id=lead.request_seller_id,
        )

    lead.review_status = SellerLead.REVIEW_REJECTED
    lead.rejected_at = now
    lead.reviewed_at = lead.reviewed_at or now
    lead.save(update_fields=['review_status', 'rejected_at', 'reviewed_at', 'updated_at'])

    message = f'{label} отклонён'
    if had_seller:
        message += '; рабочий продавец заявок сохранён (не удалён)'

    return WorkflowActionResult(
        kind=WorkflowResultKind.SUCCESS,
        message=message,
        lead_id=lead.pk,
        seller_id=lead.request_seller_id,
    )


def return_lead_to_review(lead: SellerLead) -> WorkflowActionResult:
    label = _lead_label(lead)

    marketplace_cleared = (
        lead.marketplace_invitation_status == SellerLead.MARKETPLACE_INVITATION_NONE
        and lead.marketplace_invitation_planned_at is None
    )
    if (
        lead.review_status == SellerLead.REVIEW_NEEDS_REVIEW
        and not lead.rejected_at
        and marketplace_cleared
    ):
        return WorkflowActionResult(
            kind=WorkflowResultKind.WARNING,
            message=f'{label} уже на проверке',
            lead_id=lead.pk,
            seller_id=lead.request_seller_id,
        )

    lead.review_status = SellerLead.REVIEW_NEEDS_REVIEW
    lead.rejected_at = None
    lead.marketplace_invitation_status = SellerLead.MARKETPLACE_INVITATION_NONE
    lead.marketplace_invitation_planned_at = None
    lead.save(
        update_fields=[
            'review_status',
            'rejected_at',
            'marketplace_invitation_status',
            'marketplace_invitation_planned_at',
            'updated_at',
        ],
    )

    return WorkflowActionResult(
        kind=WorkflowResultKind.SUCCESS,
        message=f'{label} возвращён на проверку',
        lead_id=lead.pk,
        seller_id=lead.request_seller_id,
    )
