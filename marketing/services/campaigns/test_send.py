from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from core.whatsapp_template_sender import (
    send_whatsapp_template_message,
    wa_template_param,
)
from marketing.models import (
    MarketingCampaign,
    MarketingCampaignMessage,
    MarketingCampaignSendRun,
)
from marketing.services.campaigns.campaign_lock import lock_campaign_for_send
from marketing.services.campaigns.send_constants import (
    MESSAGE_STATUS_FAILED,
    MESSAGE_STATUS_PENDING,
    MESSAGE_STATUS_SENT,
    MESSAGE_STATUS_SKIPPED,
    SEND_MODE_TEST,
    SEND_RUN_STATUS_COMPLETED,
    SEND_RUN_STATUS_COMPLETED_WITH_ERRORS,
    SEND_RUN_STATUS_FAILED,
    SEND_RUN_STATUS_RUNNING,
)
from marketing.services.campaigns.send_validation import (
    TestSendValidationError,
    ensure_test_send_not_already_executed,
    get_eligible_test_recipients,
    validate_test_send_executable,
)
from marketing.services.campaigns.send_variables import (
    VariableResolutionError,
    resolve_template_variables_for_recipient,
)

logger = logging.getLogger(__name__)

SendCallable = Callable[..., dict]


@dataclass(frozen=True)
class TestSendExecutionResult:
    send_run_id: int
    total_count: int
    sent_count: int
    failed_count: int
    skipped_count: int
    status: str
    blocked: bool = False
    error_message: str = ''


@dataclass(frozen=True)
class _PendingSendItem:
    message_id: int
    recipient_id: int
    phone_normalized: str
    variables: dict[str, str]
    skipped: bool


def _extract_meta_error(result: dict) -> tuple[str, str]:
    error_payload = result.get('error')
    error_code = ''
    error_message = ''
    if isinstance(error_payload, dict):
        error_obj = error_payload.get('error') or error_payload
        if isinstance(error_obj, dict):
            error_code = str(error_obj.get('code') or '')
            error_message = str(error_obj.get('message') or error_obj.get('error_user_msg') or '')
        else:
            error_message = str(error_payload)
    elif error_payload is not None:
        error_message = str(error_payload)
    if not error_message:
        error_message = 'WhatsApp send failed'
    return error_code[:64], error_message[:2000]


def _build_body_parameters(template, variables: dict[str, str]) -> list[dict]:
    parameters: list[dict] = []
    for variable in template.variables or []:
        key = variable.get('key', '')
        parameters.append(wa_template_param(variables.get(key, '')))
    return parameters


def _recipient_already_sent(campaign_id: int, recipient_id: int) -> bool:
    return MarketingCampaignMessage.objects.filter(
        send_run__campaign_id=campaign_id,
        send_run__mode=SEND_MODE_TEST,
        campaign_recipient_id=recipient_id,
        status=MESSAGE_STATUS_SENT,
    ).exists()


def _lock_campaign_for_test_send(campaign_id: int) -> MarketingCampaign:
    """Lock campaign row only; load nullable FKs via separate queries."""
    return lock_campaign_for_send(campaign_id)


def _reserve_test_send_run(
    campaign_id: int,
    *,
    created_by,
) -> tuple[int, object, list[_PendingSendItem], int]:
    try:
        with transaction.atomic():
            campaign = _lock_campaign_for_test_send(campaign_id)
            preflight = validate_test_send_executable(campaign)
            ensure_test_send_not_already_executed(campaign)

            template = campaign.message_template
            if template is None:
                raise TestSendValidationError('Шаблон не выбран.')

            recipient_by_id = {
                recipient.pk: recipient
                for recipient in get_eligible_test_recipients(campaign)
            }
            missing_recipient_ids = [
                preview.recipient_id
                for preview in preflight.recipients
                if preview.recipient_id not in recipient_by_id
            ]
            if missing_recipient_ids:
                raise TestSendValidationError(
                    'Снимок получателей изменился. Обновите preflight и повторите попытку.',
                )

            send_run = MarketingCampaignSendRun.objects.create(
                campaign=campaign,
                template=template,
                mode=SEND_MODE_TEST,
                status=SEND_RUN_STATUS_RUNNING,
                total_count=len(preflight.recipients),
                created_by=created_by,
                started_at=timezone.now(),
            )

            pending_items: list[_PendingSendItem] = []
            for preview in preflight.recipients:
                recipient = recipient_by_id[preview.recipient_id]
                if _recipient_already_sent(campaign.pk, recipient.pk):
                    MarketingCampaignMessage.objects.create(
                        send_run=send_run,
                        campaign_recipient=recipient,
                        phone_normalized=recipient.phone_normalized,
                        template_name=template.meta_template_name,
                        language_code=template.language_code,
                        variables={},
                        status=MESSAGE_STATUS_SKIPPED,
                        error_message='Already sent in previous run.',
                        attempted_at=timezone.now(),
                    )
                    pending_items.append(
                        _PendingSendItem(
                            message_id=0,
                            recipient_id=recipient.pk,
                            phone_normalized=recipient.phone_normalized,
                            variables={},
                            skipped=True,
                        ),
                    )
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
                        error_message=str(exc)[:2000],
                        attempted_at=timezone.now(),
                    )
                    pending_items.append(
                        _PendingSendItem(
                            message_id=0,
                            recipient_id=recipient.pk,
                            phone_normalized=recipient.phone_normalized,
                            variables={},
                            skipped=True,
                        ),
                    )
                    continue

                message = MarketingCampaignMessage.objects.create(
                    send_run=send_run,
                    campaign_recipient=recipient,
                    phone_normalized=recipient.phone_normalized,
                    template_name=template.meta_template_name,
                    language_code=template.language_code,
                    variables=variables,
                    status=MESSAGE_STATUS_PENDING,
                )
                pending_items.append(
                    _PendingSendItem(
                        message_id=message.pk,
                        recipient_id=recipient.pk,
                        phone_normalized=recipient.phone_normalized,
                        variables=variables,
                        skipped=False,
                    ),
                )

            return send_run.pk, template, pending_items, send_run.total_count
    except TestSendValidationError:
        raise
    except IntegrityError as exc:
        logger.warning(
            'Marketing TEST send reservation failed for campaign #%s: %s',
            campaign_id,
            exc.__class__.__name__,
        )
        raise TestSendValidationError(
            'Не удалось зарезервировать TEST-отправку. Повторите позже или обратитесь к администратору.',
        ) from exc
    except DatabaseError as exc:
        logger.warning(
            'Marketing TEST send database error during reservation for campaign #%s: %s',
            campaign_id,
            exc.__class__.__name__,
        )
        raise TestSendValidationError(
            'TEST-отправка временно недоступна из-за ошибки базы данных. Проверьте миграции marketing 0005–0007.',
        ) from exc


def execute_test_campaign_send(
    campaign_id: int,
    *,
    created_by,
    send_callable: SendCallable | None = None,
) -> TestSendExecutionResult:
    send_callable = send_callable or send_whatsapp_template_message

    send_run_id, template_for_send, pending_items, total_count = _reserve_test_send_run(
        campaign_id,
        created_by=created_by,
    )

    sent_count = 0
    failed_count = 0
    skipped_count = sum(1 for item in pending_items if item.skipped)

    for item in pending_items:
        if item.skipped:
            continue

        if _recipient_already_sent(campaign_id, item.recipient_id):
            MarketingCampaignMessage.objects.filter(pk=item.message_id).update(
                status=MESSAGE_STATUS_SKIPPED,
                error_message='Already sent in previous run.',
                attempted_at=timezone.now(),
            )
            skipped_count += 1
            continue

        body_parameters = _build_body_parameters(template_for_send, item.variables)
        MarketingCampaignMessage.objects.filter(pk=item.message_id).update(
            attempted_at=timezone.now(),
        )
        result = send_callable(
            item.phone_normalized,
            template_name=template_for_send.meta_template_name,
            template_language=template_for_send.language_code,
            body_parameters=body_parameters,
        )

        if result.get('ok'):
            MarketingCampaignMessage.objects.filter(pk=item.message_id).update(
                status=MESSAGE_STATUS_SENT,
                meta_message_id=str(result.get('message_id') or '')[:128],
                sent_at=timezone.now(),
                error_code='',
                error_message='',
            )
            sent_count += 1
        else:
            error_code, error_message = _extract_meta_error(result)
            MarketingCampaignMessage.objects.filter(pk=item.message_id).update(
                status=MESSAGE_STATUS_FAILED,
                error_code=error_code,
                error_message=error_message,
            )
            failed_count += 1
            logger.warning(
                'Marketing TEST send failed for campaign #%s recipient %s',
                campaign_id,
                item.phone_normalized[:3] + '***',
            )

    if sent_count == 0 and failed_count == 0:
        final_status = SEND_RUN_STATUS_FAILED
    elif failed_count or skipped_count:
        final_status = SEND_RUN_STATUS_COMPLETED_WITH_ERRORS
    else:
        final_status = SEND_RUN_STATUS_COMPLETED

    with transaction.atomic():
        send_run = MarketingCampaignSendRun.objects.select_for_update().get(pk=send_run_id)
        send_run.sent_count = sent_count
        send_run.failed_count = failed_count
        send_run.skipped_count = skipped_count
        send_run.status = final_status
        send_run.finished_at = timezone.now()
        send_run.save(update_fields=[
            'sent_count',
            'failed_count',
            'skipped_count',
            'status',
            'finished_at',
        ])

    return TestSendExecutionResult(
        send_run_id=send_run_id,
        total_count=total_count,
        sent_count=sent_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        status=final_status,
    )
