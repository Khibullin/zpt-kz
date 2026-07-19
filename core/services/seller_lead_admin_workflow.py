from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from django.db import transaction
from django.utils import timezone

from core.models import Seller, SellerLead, SellerLeadContactCandidate, TRANSPORT_CHOICES


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


def convert_lead_to_request_seller(lead: SellerLead) -> WorkflowActionResult:
    label = _lead_label(lead)

    if has_unresolved_contact_conflict(lead):
        return WorkflowActionResult(
            kind=WorkflowResultKind.ERROR,
            message=(
                f'{label}: невозможно добавить — '
                'не выбран основной WhatsApp: требуется разрешить конфликт контактов'
            ),
            lead_id=lead.pk,
        )

    whatsapp = normalize_request_seller_whatsapp(lead.whatsapp)
    if not whatsapp:
        return WorkflowActionResult(
            kind=WorkflowResultKind.WARNING,
            message=f'{label}: невозможно добавить — WhatsApp отсутствует',
            lead_id=lead.pk,
        )

    if not lead.name.strip():
        return WorkflowActionResult(
            kind=WorkflowResultKind.ERROR,
            message=f'{label}: невозможно добавить — отсутствует название',
            lead_id=lead.pk,
        )

    existing_seller = find_request_seller_by_whatsapp(whatsapp)
    now = timezone.now()
    update_fields = ['reviewed_at']

    if lead.request_seller_id:
        if existing_seller and lead.request_seller_id == existing_seller.pk:
            lead.reviewed_at = lead.reviewed_at or now
            _save_lead_workflow_fields(lead, update_fields=update_fields)
            return WorkflowActionResult(
                kind=WorkflowResultKind.WARNING,
                message=f'{label} уже связан с существующим продавцом',
                lead_id=lead.pk,
                seller_id=existing_seller.pk,
                linked_existing_seller=True,
            )
        if existing_seller and lead.request_seller_id != existing_seller.pk:
            lead.request_seller = existing_seller
            update_fields.append('request_seller')
            lead.reviewed_at = lead.reviewed_at or now
            _save_lead_workflow_fields(lead, update_fields=update_fields)
            return WorkflowActionResult(
                kind=WorkflowResultKind.WARNING,
                message=f'{label}: продавец уже существует, выполнено связывание',
                lead_id=lead.pk,
                seller_id=existing_seller.pk,
                linked_existing_seller=True,
            )
        lead.reviewed_at = lead.reviewed_at or now
        _save_lead_workflow_fields(lead, update_fields=update_fields)
        return WorkflowActionResult(
            kind=WorkflowResultKind.WARNING,
            message=f'{label} уже связан с существующим продавцом',
            lead_id=lead.pk,
            seller_id=lead.request_seller_id,
            linked_existing_seller=True,
        )

    if existing_seller:
        lead.request_seller = existing_seller
        lead.reviewed_at = now
        _save_lead_workflow_fields(
            lead,
            update_fields=['request_seller', 'reviewed_at'],
        )
        return WorkflowActionResult(
            kind=WorkflowResultKind.WARNING,
            message=f'{label}: продавец уже существует, выполнено связывание',
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
            receive_requests=False,
        )
        lead.request_seller = seller
        lead.reviewed_at = now
        _save_lead_workflow_fields(
            lead,
            update_fields=['request_seller', 'reviewed_at'],
        )

    return WorkflowActionResult(
        kind=WorkflowResultKind.SUCCESS,
        message=f'{label} успешно добавлен в продавцы заявок',
        lead_id=lead.pk,
        seller_id=seller.pk,
        created_seller=True,
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
