from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from core.buyer_contact_admin_filters import marketing_consent_label
from core.models import (
    BUYER_BROADCAST_MODE_TEST,
    BUYER_BROADCAST_RECIPIENT_FAILED,
    BUYER_BROADCAST_RECIPIENT_QUEUED,
    BUYER_BROADCAST_RECIPIENT_SENDING,
    BUYER_BROADCAST_RECIPIENT_SENT,
    BUYER_BROADCAST_RECIPIENT_SKIPPED,
    BUYER_BROADCAST_STATUS_CANCELLED,
    BUYER_BROADCAST_STATUS_COMPLETED,
    BUYER_BROADCAST_STATUS_COMPLETED_WITH_ERRORS,
    BUYER_BROADCAST_STATUS_DRAFT,
    BUYER_BROADCAST_STATUS_QUEUED,
    BUYER_BROADCAST_STATUS_READY,
    BUYER_BROADCAST_STATUS_SENDING,
    BUYER_CONTACT_STATUS_ACTIVE,
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    BuyerBroadcastCampaign,
    BuyerBroadcastRecipient,
    BuyerContact,
    ContactConsent,
)
from core.phone_utils import normalize_kz_phone
from core.services.buyer_broadcast_settings import (
    buyer_test_broadcast_enabled,
    get_buyer_broadcast_mode,
    get_buyer_broadcast_test_max_recipients,
)
from core.services.buyer_contact_utils import mask_phone
from core.whatsapp_template_sender import (
    send_whatsapp_template_message,
    wa_template_param,
)

logger = logging.getLogger(__name__)

SKIP_NOT_TEST_CONTACT = 'not_test_contact'
SKIP_INACTIVE_STATUS = 'inactive_status'
SKIP_INVALID_PHONE = 'invalid_phone'
SKIP_MARKETING_UNKNOWN = 'marketing_unknown'
SKIP_MARKETING_REVOKED = 'marketing_revoked'
SKIP_MARKETING_MISSING = 'marketing_missing'
SKIP_RECIPIENT_LIMIT = 'recipient_limit_exceeded'
SKIP_BROADCAST_MODE_OFF = 'broadcast_mode_off'
SKIP_CAMPAIGN_NOT_TEST = 'campaign_not_test'
SKIP_CAMPAIGN_STATUS = 'campaign_status_invalid'
SKIP_PHONE_MISMATCH = 'phone_snapshot_mismatch'
SKIP_ALREADY_PROCESSED = 'already_processed'

TERMINAL_RECIPIENT_STATUSES = {
    BUYER_BROADCAST_RECIPIENT_SENT,
    BUYER_BROADCAST_RECIPIENT_FAILED,
    BUYER_BROADCAST_RECIPIENT_SKIPPED,
}


@dataclass(frozen=True)
class BuyerBroadcastContactPreview:
    buyer_id: int
    masked_phone: str
    primary_city: str
    requests_count: int
    marketing_consent_status: str
    eligible: bool
    skip_reason: str


@dataclass(frozen=True)
class BuyerBroadcastPreparationResult:
    campaign_id: int
    selected_count: int
    eligible_count: int
    skipped_test_flag_count: int
    skipped_status_count: int
    skipped_consent_count: int
    created_recipient_count: int
    existing_recipient_count: int
    errors: tuple[str, ...]
    contacts: tuple[BuyerBroadcastContactPreview, ...] = ()


@dataclass(frozen=True)
class BuyerBroadcastSendResult:
    recipient_id: int
    ok: bool
    status: str
    provider_message_id: str
    error_message: str
    skipped: bool
    skip_reason: str


@dataclass(frozen=True)
class BuyerBroadcastProcessResult:
    campaign_id: int
    processed_count: int
    sent_count: int
    failed_count: int
    skipped_count: int
    final_status: str
    errors: tuple[str, ...]


def _marketing_consent_subquery():
    return ContactConsent.objects.filter(
        buyer=OuterRef('pk'),
        channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
        purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
    )


def _get_marketing_consent_status(buyer: BuyerContact) -> tuple[str, str | None]:
    consent = ContactConsent.objects.filter(
        buyer=buyer,
        channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
        purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
    ).order_by('-updated_at', '-id').first()
    if consent is None:
        return marketing_consent_label(None), None
    return marketing_consent_label(consent.status), consent.status


def _evaluate_buyer_for_campaign(
    buyer: BuyerContact,
    *,
    enforce_limit: bool,
    eligible_so_far: int,
    max_recipients: int,
) -> tuple[bool, str]:
    if not buyer.is_test_contact:
        return False, SKIP_NOT_TEST_CONTACT
    if buyer.status != BUYER_CONTACT_STATUS_ACTIVE:
        return False, SKIP_INACTIVE_STATUS
    normalized_phone = normalize_kz_phone(buyer.phone_normalized)
    if not normalized_phone:
        return False, SKIP_INVALID_PHONE
    _, consent_status = _get_marketing_consent_status(buyer)
    if consent_status is None:
        return False, SKIP_MARKETING_MISSING
    if consent_status == CONTACT_CONSENT_STATUS_UNKNOWN:
        return False, SKIP_MARKETING_UNKNOWN
    if consent_status == CONTACT_CONSENT_STATUS_REVOKED:
        return False, SKIP_MARKETING_REVOKED
    if consent_status != CONTACT_CONSENT_STATUS_GRANTED:
        return False, SKIP_MARKETING_MISSING
    if enforce_limit and eligible_so_far >= max_recipients:
        return False, SKIP_RECIPIENT_LIMIT
    return True, ''


def _build_contact_preview(
    buyer: BuyerContact,
    *,
    enforce_limit: bool,
    eligible_so_far: int,
    max_recipients: int,
) -> BuyerBroadcastContactPreview:
    marketing_label, _ = _get_marketing_consent_status(buyer)
    eligible, skip_reason = _evaluate_buyer_for_campaign(
        buyer,
        enforce_limit=enforce_limit,
        eligible_so_far=eligible_so_far,
        max_recipients=max_recipients,
    )
    return BuyerBroadcastContactPreview(
        buyer_id=buyer.pk,
        masked_phone=mask_phone(buyer.phone_normalized),
        primary_city=buyer.primary_city,
        requests_count=buyer.requests_count,
        marketing_consent_status=marketing_label,
        eligible=eligible,
        skip_reason=skip_reason,
    )


def _validate_campaign_environment(
    campaign: BuyerBroadcastCampaign,
    *,
    require_prepare_status: bool,
) -> tuple[bool, tuple[str, ...]]:
    errors: list[str] = []
    if get_buyer_broadcast_mode() != 'TEST':
        errors.append('BUYER_BROADCAST_MODE должен быть TEST.')
    if campaign.mode != BUYER_BROADCAST_MODE_TEST:
        errors.append('Кампания должна быть в тестовом режиме.')
    if not str(campaign.template_name or '').strip():
        errors.append('Имя шаблона WhatsApp обязательно.')
    if require_prepare_status and campaign.status not in {
        BUYER_BROADCAST_STATUS_DRAFT,
        BUYER_BROADCAST_STATUS_READY,
        BUYER_BROADCAST_STATUS_QUEUED,
    }:
        errors.append('Кампанию можно подготовить только из статуса draft/ready/queued.')
    return not errors, tuple(errors)


def _analyze_selected_contacts(
    campaign: BuyerBroadcastCampaign,
    *,
    enforce_limit: bool,
) -> BuyerBroadcastPreparationResult:
    max_recipients = get_buyer_broadcast_test_max_recipients()
    selected = list(
        campaign.test_contacts.order_by('id').select_related(),
    )
    selected_count = len(selected)
    errors: list[str] = []
    env_ok, env_errors = _validate_campaign_environment(
        campaign,
        require_prepare_status=False,
    )
    errors.extend(env_errors)

    if selected_count > max_recipients:
        errors.append(
            f'Выбрано {selected_count} контактов, лимит TEST — {max_recipients}.',
        )

    skipped_test_flag_count = 0
    skipped_status_count = 0
    skipped_consent_count = 0
    eligible_count = 0
    previews: list[BuyerBroadcastContactPreview] = []

    for buyer in selected:
        preview = _build_contact_preview(
            buyer,
            enforce_limit=enforce_limit,
            eligible_so_far=eligible_count,
            max_recipients=max_recipients,
        )
        previews.append(preview)
        if preview.eligible:
            eligible_count += 1
            continue
        if preview.skip_reason == SKIP_NOT_TEST_CONTACT:
            skipped_test_flag_count += 1
        elif preview.skip_reason in {SKIP_INACTIVE_STATUS, SKIP_INVALID_PHONE}:
            skipped_status_count += 1
        elif preview.skip_reason:
            skipped_consent_count += 1

    return BuyerBroadcastPreparationResult(
        campaign_id=campaign.pk,
        selected_count=selected_count,
        eligible_count=eligible_count,
        skipped_test_flag_count=skipped_test_flag_count,
        skipped_status_count=skipped_status_count,
        skipped_consent_count=skipped_consent_count,
        created_recipient_count=0,
        existing_recipient_count=0,
        errors=tuple(errors),
        contacts=tuple(previews),
    )


def preview_test_campaign(campaign: BuyerBroadcastCampaign) -> BuyerBroadcastPreparationResult:
    return _analyze_selected_contacts(campaign, enforce_limit=True)


def _build_template_body_parameters(campaign: BuyerBroadcastCampaign) -> list[dict]:
    raw_params = campaign.template_body_parameters or []
    return [wa_template_param(value) for value in raw_params]


@transaction.atomic
def prepare_test_campaign(
    campaign: BuyerBroadcastCampaign,
) -> BuyerBroadcastPreparationResult:
    campaign = BuyerBroadcastCampaign.objects.select_for_update().get(pk=campaign.pk)
    analysis = _analyze_selected_contacts(campaign, enforce_limit=True)
    errors = list(analysis.errors)

    env_ok, env_errors = _validate_campaign_environment(
        campaign,
        require_prepare_status=True,
    )
    for error in env_errors:
        if error not in errors:
            errors.append(error)

    if not buyer_test_broadcast_enabled():
        errors.append('BUYER_BROADCAST_MODE должен быть TEST.')

    if analysis.eligible_count == 0:
        errors.append('Нет допустимых тестовых получателей для очереди.')

    if errors:
        return BuyerBroadcastPreparationResult(
            campaign_id=campaign.pk,
            selected_count=analysis.selected_count,
            eligible_count=analysis.eligible_count,
            skipped_test_flag_count=analysis.skipped_test_flag_count,
            skipped_status_count=analysis.skipped_status_count,
            skipped_consent_count=analysis.skipped_consent_count,
            created_recipient_count=0,
            existing_recipient_count=0,
            errors=tuple(errors),
            contacts=analysis.contacts,
        )

    now = timezone.now()
    created_count = 0
    existing_count = 0
    eligible_so_far = 0
    max_recipients = get_buyer_broadcast_test_max_recipients()

    for buyer in campaign.test_contacts.order_by('id'):
        eligible, skip_reason = _evaluate_buyer_for_campaign(
            buyer,
            enforce_limit=True,
            eligible_so_far=eligible_so_far,
            max_recipients=max_recipients,
        )
        if eligible:
            eligible_so_far += 1
            normalized_phone = normalize_kz_phone(buyer.phone_normalized) or ''
            recipient, created = BuyerBroadcastRecipient.objects.get_or_create(
                campaign=campaign,
                buyer=buyer,
                defaults={
                    'phone_snapshot': normalized_phone,
                    'masked_phone_snapshot': mask_phone(normalized_phone),
                    'status': BUYER_BROADCAST_RECIPIENT_QUEUED,
                    'queued_at': now,
                },
            )
            if created:
                created_count += 1
            else:
                existing_count += 1
                if recipient.status == BUYER_BROADCAST_RECIPIENT_SENT:
                    continue
                if recipient.status != BUYER_BROADCAST_RECIPIENT_QUEUED:
                    recipient.status = BUYER_BROADCAST_RECIPIENT_QUEUED
                    recipient.skip_reason = ''
                    recipient.error_message = ''
                    recipient.queued_at = now
                    recipient.phone_snapshot = normalized_phone
                    recipient.masked_phone_snapshot = mask_phone(normalized_phone)
                    recipient.save(
                        update_fields=[
                            'status',
                            'skip_reason',
                            'error_message',
                            'queued_at',
                            'phone_snapshot',
                            'masked_phone_snapshot',
                            'updated_at',
                        ],
                    )
            continue

        recipient, created = BuyerBroadcastRecipient.objects.get_or_create(
            campaign=campaign,
            buyer=buyer,
            defaults={
                'phone_snapshot': buyer.phone_normalized,
                'masked_phone_snapshot': mask_phone(buyer.phone_normalized),
                'status': BUYER_BROADCAST_RECIPIENT_SKIPPED,
                'skip_reason': skip_reason,
                'queued_at': now,
            },
        )
        if created:
            created_count += 1
        elif recipient.status not in TERMINAL_RECIPIENT_STATUSES:
            recipient.status = BUYER_BROADCAST_RECIPIENT_SKIPPED
            recipient.skip_reason = skip_reason
            recipient.save(update_fields=['status', 'skip_reason', 'updated_at'])

    campaign.status = BUYER_BROADCAST_STATUS_QUEUED
    campaign.queued_at = now
    campaign.save(update_fields=['status', 'queued_at', 'updated_at'])

    return BuyerBroadcastPreparationResult(
        campaign_id=campaign.pk,
        selected_count=analysis.selected_count,
        eligible_count=analysis.eligible_count,
        skipped_test_flag_count=analysis.skipped_test_flag_count,
        skipped_status_count=analysis.skipped_status_count,
        skipped_consent_count=analysis.skipped_consent_count,
        created_recipient_count=created_count,
        existing_recipient_count=existing_count,
        errors=(),
        contacts=analysis.contacts,
    )


def _validate_recipient_before_send(
    recipient: BuyerBroadcastRecipient,
) -> tuple[bool, str]:
    if not buyer_test_broadcast_enabled():
        return False, SKIP_BROADCAST_MODE_OFF
    campaign = recipient.campaign
    if campaign.mode != BUYER_BROADCAST_MODE_TEST:
        return False, SKIP_CAMPAIGN_NOT_TEST
    if campaign.status not in {
        BUYER_BROADCAST_STATUS_QUEUED,
        BUYER_BROADCAST_STATUS_SENDING,
    }:
        return False, SKIP_CAMPAIGN_STATUS
    buyer = recipient.buyer
    if not buyer.is_test_contact:
        return False, SKIP_NOT_TEST_CONTACT
    if buyer.status != BUYER_CONTACT_STATUS_ACTIVE:
        return False, SKIP_INACTIVE_STATUS
    _, consent_status = _get_marketing_consent_status(buyer)
    if consent_status != CONTACT_CONSENT_STATUS_GRANTED:
        if consent_status == CONTACT_CONSENT_STATUS_UNKNOWN:
            return False, SKIP_MARKETING_UNKNOWN
        if consent_status == CONTACT_CONSENT_STATUS_REVOKED:
            return False, SKIP_MARKETING_REVOKED
        return False, SKIP_MARKETING_MISSING
    normalized_phone = normalize_kz_phone(buyer.phone_normalized)
    if not normalized_phone:
        return False, SKIP_INVALID_PHONE
    if recipient.phone_snapshot != normalized_phone:
        return False, SKIP_PHONE_MISMATCH
    return True, ''


def send_buyer_broadcast_recipient(
    recipient: BuyerBroadcastRecipient,
) -> BuyerBroadcastSendResult:
    recipient = BuyerBroadcastRecipient.objects.select_related(
        'campaign',
        'buyer',
    ).get(pk=recipient.pk)

    if recipient.status in {
        BUYER_BROADCAST_RECIPIENT_SENT,
        BUYER_BROADCAST_RECIPIENT_SKIPPED,
    }:
        return BuyerBroadcastSendResult(
            recipient_id=recipient.pk,
            ok=False,
            status=recipient.status,
            provider_message_id=recipient.provider_message_id,
            error_message=recipient.error_message,
            skipped=True,
            skip_reason=recipient.skip_reason or SKIP_ALREADY_PROCESSED,
        )

    allowed, skip_reason = _validate_recipient_before_send(recipient)
    now = timezone.now()
    if not allowed:
        recipient.status = BUYER_BROADCAST_RECIPIENT_SKIPPED
        recipient.skip_reason = skip_reason
        recipient.last_attempt_at = now
        recipient.save(
            update_fields=[
                'status',
                'skip_reason',
                'last_attempt_at',
                'updated_at',
            ],
        )
        logger.info(
            'Buyer broadcast recipient #%s skipped: %s',
            recipient.pk,
            skip_reason,
        )
        return BuyerBroadcastSendResult(
            recipient_id=recipient.pk,
            ok=False,
            status=recipient.status,
            provider_message_id='',
            error_message='',
            skipped=True,
            skip_reason=skip_reason,
        )

    recipient.status = BUYER_BROADCAST_RECIPIENT_SENDING
    recipient.attempts_count += 1
    recipient.last_attempt_at = now
    recipient.save(
        update_fields=[
            'status',
            'attempts_count',
            'last_attempt_at',
            'updated_at',
        ],
    )

    campaign = recipient.campaign
    wa_result = send_whatsapp_template_message(
        recipient.phone_snapshot,
        template_name=campaign.template_name,
        template_language=campaign.template_language,
        body_parameters=_build_template_body_parameters(campaign),
    )

    if wa_result.get('ok'):
        recipient.status = BUYER_BROADCAST_RECIPIENT_SENT
        recipient.provider_message_id = wa_result.get('message_id') or ''
        recipient.error_message = ''
        recipient.sent_at = now
        recipient.save(
            update_fields=[
                'status',
                'provider_message_id',
                'error_message',
                'sent_at',
                'updated_at',
            ],
        )
        logger.info(
            'Buyer broadcast campaign #%s recipient #%s sent',
            campaign.pk,
            recipient.pk,
        )
        return BuyerBroadcastSendResult(
            recipient_id=recipient.pk,
            ok=True,
            status=recipient.status,
            provider_message_id=recipient.provider_message_id,
            error_message='',
            skipped=False,
            skip_reason='',
        )

    error_message = wa_result.get('error')
    if not isinstance(error_message, str):
        error_message = str(error_message)
    recipient.status = BUYER_BROADCAST_RECIPIENT_FAILED
    recipient.error_message = error_message
    recipient.save(update_fields=['status', 'error_message', 'updated_at'])
    status_code = wa_result.get('status_code')
    logger.warning(
        'Buyer broadcast recipient #%s failed: HTTP %s',
        recipient.pk,
        status_code or 'n/a',
    )
    return BuyerBroadcastSendResult(
        recipient_id=recipient.pk,
        ok=False,
        status=recipient.status,
        provider_message_id='',
        error_message=error_message,
        skipped=False,
        skip_reason='',
    )


def _resolve_campaign_final_status(campaign: BuyerBroadcastCampaign) -> str:
    recipients = campaign.recipients.all()
    if recipients.filter(status=BUYER_BROADCAST_RECIPIENT_FAILED).exists():
        return BUYER_BROADCAST_STATUS_COMPLETED_WITH_ERRORS
    return BUYER_BROADCAST_STATUS_COMPLETED


@transaction.atomic
def process_buyer_broadcast_campaign(
    campaign: BuyerBroadcastCampaign,
    *,
    limit: int | None = None,
    recipient_id: int | None = None,
) -> BuyerBroadcastProcessResult:
    campaign = BuyerBroadcastCampaign.objects.select_for_update().get(pk=campaign.pk)
    errors: list[str] = []

    if campaign.status in {
        BUYER_BROADCAST_STATUS_COMPLETED,
        BUYER_BROADCAST_STATUS_COMPLETED_WITH_ERRORS,
        BUYER_BROADCAST_STATUS_CANCELLED,
    }:
        return BuyerBroadcastProcessResult(
            campaign_id=campaign.pk,
            processed_count=0,
            sent_count=0,
            failed_count=0,
            skipped_count=0,
            final_status=campaign.status,
            errors=(f'Кампания #{campaign.pk} уже завершена или отменена.',),
        )

    if not buyer_test_broadcast_enabled():
        return BuyerBroadcastProcessResult(
            campaign_id=campaign.pk,
            processed_count=0,
            sent_count=0,
            failed_count=0,
            skipped_count=0,
            final_status=campaign.status,
            errors=('BUYER_BROADCAST_MODE должен быть TEST.',),
        )

    if campaign.status != BUYER_BROADCAST_STATUS_QUEUED:
        errors.append('Кампания должна быть в статусе queued перед отправкой.')

    recipients_qs = campaign.recipients.filter(
        status=BUYER_BROADCAST_RECIPIENT_QUEUED,
    ).order_by('id')
    if recipient_id is not None:
        recipients_qs = recipients_qs.filter(pk=recipient_id)
    if limit is not None:
        recipients_qs = recipients_qs[:limit]

    recipients = list(recipients_qs.select_related('buyer', 'campaign'))
    if not recipients and not errors:
        errors.append('Нет получателей в очереди для отправки.')

    if errors:
        return BuyerBroadcastProcessResult(
            campaign_id=campaign.pk,
            processed_count=0,
            sent_count=0,
            failed_count=0,
            skipped_count=0,
            final_status=campaign.status,
            errors=tuple(errors),
        )

    now = timezone.now()
    if campaign.status == BUYER_BROADCAST_STATUS_QUEUED:
        campaign.status = BUYER_BROADCAST_STATUS_SENDING
        campaign.started_at = now
        campaign.save(update_fields=['status', 'started_at', 'updated_at'])

    sent_count = 0
    failed_count = 0
    skipped_count = 0

    for recipient in recipients:
        result = send_buyer_broadcast_recipient(recipient)
        if result.skipped:
            skipped_count += 1
        elif result.ok:
            sent_count += 1
        else:
            failed_count += 1

    final_status = _resolve_campaign_final_status(campaign)
    campaign.status = final_status
    campaign.completed_at = timezone.now()
    campaign.save(update_fields=['status', 'completed_at', 'updated_at'])

    return BuyerBroadcastProcessResult(
        campaign_id=campaign.pk,
        processed_count=len(recipients),
        sent_count=sent_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        final_status=final_status,
        errors=(),
    )
