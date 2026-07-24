from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from marketing.models import MarketingCampaign, MarketingCampaignMessage, MarketingCampaignSendRun
from marketing.services.campaigns.live_consent import recheck_live_recipient_consent
from marketing.services.campaigns.live_send_validation import (
    LiveSendValidationError,
    campaign_recipient_already_live_sent,
    campaign_recipient_live_in_flight,
    validate_live_send_confirmation,
    validate_live_send_executable,
)
from marketing.services.campaigns.send_constants import (
    ERROR_CODE_CONSENT_NOT_GRANTED,
    ERROR_CODE_CONSENT_REVOKED,
    MESSAGE_STATUS_QUEUED,
    MESSAGE_STATUS_SKIPPED,
    SEND_MODE_LIVE,
    SEND_RUN_STATUS_QUEUED,
)
from marketing.services.campaigns.send_variables import (
    VariableResolutionError,
    resolve_template_variables_for_recipient,
)
from marketing.services.campaigns.campaign_lock import lock_campaign_for_send

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveSendQueueResult:
    send_run_id: int
    total_count: int
    queued_count: int
    skipped_count: int
    status: str


def _skip_reason_to_error_code(reason: str) -> str:
    if reason == 'consent_revoked':
        return ERROR_CODE_CONSENT_REVOKED
    return ERROR_CODE_CONSENT_NOT_GRANTED


def create_live_send_queue(
    campaign_id: int,
    *,
    created_by,
    confirmation_text: str,
) -> LiveSendQueueResult:
    try:
        with transaction.atomic():
            campaign = lock_campaign_for_send(campaign_id)
            validate_live_send_confirmation(campaign, confirmation_text)
            preflight = validate_live_send_executable(campaign)

            template = campaign.message_template
            if template is None:
                raise LiveSendValidationError('Шаблон не выбран.')

            send_run = MarketingCampaignSendRun.objects.create(
                campaign=campaign,
                template=template,
                mode=SEND_MODE_LIVE,
                status=SEND_RUN_STATUS_QUEUED,
                total_count=0,
                queued_count=0,
                sent_count=0,
                failed_count=0,
                skipped_count=0,
                created_by=created_by,
                started_at=timezone.now(),
            )

            queued_count = 0
            skipped_count = 0
            recipient_ids = {item.recipient_id for item in preflight.recipients}

            for preview in preflight.recipients:
                recipient = campaign.recipients.get(pk=preview.recipient_id)
                if recipient.pk not in recipient_ids:
                    raise LiveSendValidationError(
                        'Снимок получателей изменился. Обновите preflight и повторите попытку.',
                    )

                if campaign_recipient_already_live_sent(campaign.pk, recipient.pk):
                    MarketingCampaignMessage.objects.create(
                        send_run=send_run,
                        campaign_recipient=recipient,
                        phone_normalized=recipient.phone_normalized,
                        template_name=template.meta_template_name,
                        language_code=template.language_code,
                        variables={},
                        status=MESSAGE_STATUS_SKIPPED,
                        error_code='already_sent',
                        error_message='Already sent in previous LIVE run.',
                        attempted_at=timezone.now(),
                    )
                    skipped_count += 1
                    continue

                if campaign_recipient_live_in_flight(campaign.pk, recipient.pk):
                    MarketingCampaignMessage.objects.create(
                        send_run=send_run,
                        campaign_recipient=recipient,
                        phone_normalized=recipient.phone_normalized,
                        template_name=template.meta_template_name,
                        language_code=template.language_code,
                        variables={},
                        status=MESSAGE_STATUS_SKIPPED,
                        error_code='already_queued',
                        error_message='Already queued in another LIVE run.',
                        attempted_at=timezone.now(),
                    )
                    skipped_count += 1
                    continue

                live_ok, skip_reason = recheck_live_recipient_consent(recipient)
                if not live_ok:
                    MarketingCampaignMessage.objects.create(
                        send_run=send_run,
                        campaign_recipient=recipient,
                        phone_normalized=recipient.phone_normalized,
                        template_name=template.meta_template_name,
                        language_code=template.language_code,
                        variables={},
                        status=MESSAGE_STATUS_SKIPPED,
                        error_code=_skip_reason_to_error_code(skip_reason),
                        error_message=skip_reason,
                        attempted_at=timezone.now(),
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
                        attempted_at=timezone.now(),
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
                )
                queued_count += 1

            if queued_count <= 0:
                raise LiveSendValidationError(
                    'Не удалось поставить в очередь ни одного LIVE-сообщения.',
                )

            total_count = queued_count + skipped_count
            send_run.total_count = total_count
            send_run.queued_count = queued_count
            send_run.skipped_count = skipped_count
            send_run.save(update_fields=[
                'total_count',
                'queued_count',
                'skipped_count',
            ])

            return LiveSendQueueResult(
                send_run_id=send_run.pk,
                total_count=total_count,
                queued_count=queued_count,
                skipped_count=skipped_count,
                status=SEND_RUN_STATUS_QUEUED,
            )
    except LiveSendValidationError:
        raise
    except IntegrityError as exc:
        logger.warning(
            'Marketing LIVE queue creation failed for campaign #%s: %s',
            campaign_id,
            exc.__class__.__name__,
        )
        raise LiveSendValidationError(
            'Не удалось создать LIVE-очередь. Возможно, отправка уже запущена.',
        ) from exc
    except DatabaseError as exc:
        logger.warning(
            'Marketing LIVE queue database error for campaign #%s: %s',
            campaign_id,
            exc.__class__.__name__,
        )
        raise LiveSendValidationError(
            'LIVE-отправка временно недоступна из-за ошибки базы данных.',
        ) from exc
