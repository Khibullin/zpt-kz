from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import CONTACT_CONSENT_STATUS_UNKNOWN
from marketing.models import (
    MarketingAudience,
    MarketingCampaign,
    MarketingCampaignMessage,
    MarketingCampaignRecipient,
    MarketingCampaignSendRun,
    MarketingWhatsAppTemplate,
)
from marketing.services.audiences.constants import (
    GROUP_BUYERS,
    GROUP_SELLERS,
    SUBTYPE_ALL_BUYERS,
    SUBTYPE_ALL_SELLERS,
    SUBTYPE_MARKETPLACE_PAID,
    SUBTYPE_PARTS_REQUESTS,
    SUBTYPE_REQUEST_SELLERS,
)
from marketing.services.campaigns.constants import (
    CHANNEL_WHATSAPP,
    ELIGIBILITY_ELIGIBLE,
    ELIGIBILITY_EXCLUDED,
    STATUS_AUDIENCE_PREPARED,
)
from marketing.services.campaigns.live_simple_waves import simple_mailing_has_active_run
from marketing.services.campaigns.send_constants import (
    ACTIVE_SIMPLE_MAILING_LOCK_VALUE,
    MESSAGE_STATUS_QUEUED,
    MESSAGE_STATUS_SKIPPED,
    SEND_MODE_LIVE,
    SEND_RUN_STATUS_QUEUED,
    WORKFLOW_TYPE_SIMPLE_MAILING,
)
from marketing.services.campaigns.send_settings import (
    get_marketing_simple_wave_interval_minutes,
    get_marketing_simple_wave_size,
    marketing_live_whatsapp_send_enabled,
)
from marketing.services.campaigns.send_variables import (
    VariableResolutionError,
    resolve_template_variables_for_recipient,
)
from marketing.services.simple_mailing.consent import (
    SKIP_REASON_CONSENT_REVOKED,
    evaluate_simple_mailing_phone,
)
from marketing.services.simple_mailing.launch_recipients import (
    SimpleMailingLaunchRecipient,
    resolve_simple_mailing_launch_recipients,
)
from marketing.services.simple_mailing.purpose import recipient_type_to_campaign_purpose
from marketing.services.simple_mailing.waves import compute_wave_schedule
from marketing.services.templates.constants import META_STATUS_APPROVED


class SimpleMailingLaunchError(Exception):
    pass


class SimpleMailingCountChangedError(SimpleMailingLaunchError):
    def __init__(self, *, expected: int, actual: int) -> None:
        super().__init__(f'Recipient count changed from {expected} to {actual}.')
        self.expected = expected
        self.actual = actual


@dataclass(frozen=True)
class SimpleMailingLaunchResult:
    send_run_id: int
    campaign_id: int
    queued_count: int
    skipped_count: int
    total_count: int
    idempotent_replay: bool = False


_RECIPIENT_TYPE_AUDIENCE = {
    'parts_request_buyers': (GROUP_BUYERS, SUBTYPE_PARTS_REQUESTS),
    'marketplace_buyers': (GROUP_BUYERS, SUBTYPE_MARKETPLACE_PAID),
    'sellers': (GROUP_SELLERS, SUBTYPE_REQUEST_SELLERS),
}


def ensure_launch_key(draft: dict) -> str:
    launch_key = draft.get('launch_key')
    if launch_key:
        return str(launch_key)
    return str(uuid.uuid4())


def _audience_for_recipient_type(recipient_type: str) -> tuple[str, str]:
    try:
        return _RECIPIENT_TYPE_AUDIENCE[recipient_type]
    except KeyError as exc:
        raise SimpleMailingLaunchError(f'Unknown recipient type: {recipient_type}') from exc


def _build_campaign_name(*, recipient_label: str, brands_label: str) -> str:
    today = timezone.localdate().strftime('%d.%m.%Y')
    return f'{recipient_label} — {brands_label} — {today}'


def _consent_status_for_phone(phone_normalized: str) -> str:
    from marketing.services.simple_mailing.consent import _marketing_consent_status
    from core.models import BuyerContact

    buyer = BuyerContact.objects.filter(phone_normalized=phone_normalized).first()
    if buyer is None:
        return CONTACT_CONSENT_STATUS_UNKNOWN
    return _marketing_consent_status(buyer) or CONTACT_CONSENT_STATUS_UNKNOWN


def launch_simple_mailing(
    *,
    draft: dict,
    template: MarketingWhatsAppTemplate,
    created_by,
    launch_key: str,
) -> SimpleMailingLaunchResult:
    if not marketing_live_whatsapp_send_enabled():
        raise SimpleMailingLaunchError('Отправка отключена. Режим: OFF.')

    existing = MarketingCampaignSendRun.objects.filter(
        simple_mailing_key=launch_key,
    ).first()
    if existing is not None:
        return SimpleMailingLaunchResult(
            send_run_id=existing.pk,
            campaign_id=existing.campaign_id,
            queued_count=existing.queued_count,
            skipped_count=existing.skipped_count,
            total_count=existing.total_count,
            idempotent_replay=True,
        )

    recipient_type = draft.get('recipient_type') or ''
    all_brands = bool(draft.get('all_brands'))
    brands = list(draft.get('brands') or [])
    expected_count = int(draft.get('count') or 0)

    purpose = recipient_type_to_campaign_purpose(recipient_type)
    if not template.is_active or template.meta_status != META_STATUS_APPROVED:
        raise SimpleMailingLaunchError('Выбранный шаблон недоступен для отправки.')
    if purpose not in template.allowed_purposes:
        raise SimpleMailingLaunchError('Шаблон несовместим с типом получателей.')

    launch_recipients = resolve_simple_mailing_launch_recipients(
        recipient_type=recipient_type,
        all_brands=all_brands,
        brands=brands,
    )
    actual_count = len(launch_recipients)
    if actual_count != expected_count:
        raise SimpleMailingCountChangedError(expected=expected_count, actual=actual_count)
    if actual_count <= 0:
        raise SimpleMailingLaunchError('Получатели не найдены.')

    if simple_mailing_has_active_run():
        raise SimpleMailingLaunchError(
            'Предыдущая рассылка ещё выполняется. Дождитесь её завершения.',
        )

    contact_group, contact_subtype = _audience_for_recipient_type(recipient_type)
    brands_label = 'Все марки' if all_brands else ', '.join(brands) if brands else '—'
    from marketing.services.simple_mailing.constants import RECIPIENT_TYPE_CHOICES

    recipient_label = dict(RECIPIENT_TYPE_CHOICES).get(recipient_type, recipient_type)
    campaign_name = _build_campaign_name(
        recipient_label=recipient_label,
        brands_label=brands_label,
    )

    wave_size = get_marketing_simple_wave_size()
    wave_interval = get_marketing_simple_wave_interval_minutes()
    t0 = timezone.now()
    wave_rows = compute_wave_schedule(
        total_count=actual_count,
        t0=t0,
        wave_size=wave_size,
        interval_minutes=wave_interval,
    )
    wave_by_position = {row[0]: row for row in wave_rows}

    try:
        with transaction.atomic():
            audience = MarketingAudience.objects.create(
                name=f'[Simple mailing] {campaign_name}',
                description='Автоматическая аудитория simple mailing.',
                contact_group=contact_group,
                contact_subtype=contact_subtype,
                criteria={
                    'simple_mailing': True,
                    'recipient_type': recipient_type,
                    'all_brands': all_brands,
                    'brands': brands,
                },
                is_active=False,
                created_by=created_by,
            )
            campaign = MarketingCampaign.objects.create(
                name=campaign_name,
                description='Simple mailing auto campaign.',
                audience=audience,
                purpose=purpose,
                channel=CHANNEL_WHATSAPP,
                status=STATUS_AUDIENCE_PREPARED,
                is_active=False,
                created_by=created_by,
                message_template=template,
                template_selected_at=t0,
                matched_count=actual_count,
                unique_count=actual_count,
                eligible_count=actual_count,
                audience_prepared_at=t0,
            )

            snapshot_recipients: list[MarketingCampaignRecipient] = []
            for index, row in enumerate(launch_recipients, start=1):
                consent_status = _consent_status_for_phone(row.phone_normalized)
                snapshot_recipients.append(
                    MarketingCampaignRecipient(
                        campaign=campaign,
                        phone_normalized=row.phone_normalized,
                        display_name=row.display_name,
                        city=row.city,
                        roles=[recipient_label],
                        vehicle_summary=row.brands_label,
                        is_test_contact=row.is_test_contact,
                        consent_status=consent_status,
                        eligibility_status=ELIGIBILITY_ELIGIBLE,
                        exclusion_reason='',
                        source_summary={'simple_mailing': True},
                    ),
                )
            MarketingCampaignRecipient.objects.bulk_create(snapshot_recipients)
            campaign.recipients.all()  # ensure IDs loaded in same transaction
            recipient_by_phone = {
                recipient.phone_normalized: recipient
                for recipient in campaign.recipients.all()
            }

            send_run = MarketingCampaignSendRun.objects.create(
                campaign=campaign,
                template=template,
                mode=SEND_MODE_LIVE,
                status=SEND_RUN_STATUS_QUEUED,
                workflow_type=WORKFLOW_TYPE_SIMPLE_MAILING,
                simple_mailing_key=uuid.UUID(str(launch_key)),
                active_simple_mailing_lock=ACTIVE_SIMPLE_MAILING_LOCK_VALUE,
                total_count=0,
                queued_count=0,
                sent_count=0,
                failed_count=0,
                skipped_count=0,
                created_by=created_by,
                started_at=t0,
            )

            queued_count = 0
            skipped_count = 0
            for position, launch_row in enumerate(launch_recipients, start=1):
                _, wave_number, scheduled_at = wave_by_position[position]
                recipient = recipient_by_phone[launch_row.phone_normalized]
                eligible, skip_reason = evaluate_simple_mailing_phone(
                    phone_normalized=launch_row.phone_normalized,
                    is_test=launch_row.is_test_contact,
                )
                if not eligible:
                    MarketingCampaignMessage.objects.create(
                        send_run=send_run,
                        campaign_recipient=recipient,
                        phone_normalized=recipient.phone_normalized,
                        template_name=template.meta_template_name,
                        language_code=template.language_code,
                        variables={},
                        status=MESSAGE_STATUS_SKIPPED,
                        error_code=skip_reason,
                        error_message=skip_reason,
                        wave_number=wave_number,
                        position_number=position,
                        scheduled_at=scheduled_at,
                        attempted_at=t0,
                    )
                    skipped_count += 1
                    continue

                try:
                    variables = resolve_template_variables_for_recipient(template, recipient)
                except VariableResolutionError as exc:
                    MarketingCampaignMessage.objects.create(
                        send_run=send_run,
                        campaign_recipient=recipient,
                        phone_normalized=recipient.phone_normalized,
                        template_name=template.meta_template_name,
                        language_code=template.language_code,
                        variables={},
                        status=MESSAGE_STATUS_SKIPPED,
                        error_code='missing_variable',
                        error_message=str(exc)[:2000],
                        wave_number=wave_number,
                        position_number=position,
                        scheduled_at=scheduled_at,
                        attempted_at=t0,
                    )
                    skipped_count += 1
                    continue

                MarketingCampaignMessage.objects.create(
                    send_run=send_run,
                    campaign_recipient=recipient,
                    phone_normalized=recipient.phone_normalized,
                    template_name=template.meta_template_name,
                    language_code=template.language_code,
                    variables=variables,
                    status=MESSAGE_STATUS_QUEUED,
                    wave_number=wave_number,
                    position_number=position,
                    scheduled_at=scheduled_at,
                )
                queued_count += 1

            if queued_count <= 0:
                raise SimpleMailingLaunchError(
                    'Не удалось поставить в очередь ни одного сообщения.',
                )

            total_count = queued_count + skipped_count
            send_run.total_count = total_count
            send_run.queued_count = queued_count
            send_run.skipped_count = skipped_count
            send_run.save(update_fields=['total_count', 'queued_count', 'skipped_count'])

            return SimpleMailingLaunchResult(
                send_run_id=send_run.pk,
                campaign_id=campaign.pk,
                queued_count=queued_count,
                skipped_count=skipped_count,
                total_count=total_count,
            )
    except IntegrityError as exc:
        existing = MarketingCampaignSendRun.objects.filter(simple_mailing_key=launch_key).first()
        if existing is not None:
            return SimpleMailingLaunchResult(
                send_run_id=existing.pk,
                campaign_id=existing.campaign_id,
                queued_count=existing.queued_count,
                skipped_count=existing.skipped_count,
                total_count=existing.total_count,
                idempotent_replay=True,
            )
        if 'uniq_active_simple_mailing_lock' in str(exc):
            raise SimpleMailingLaunchError(
                'Предыдущая рассылка ещё выполняется. Дождитесь её завершения.',
            ) from exc
        raise SimpleMailingLaunchError(
            'Не удалось создать рассылку. Повторите попытку.',
        ) from exc
