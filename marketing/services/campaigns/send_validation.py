from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.models import CONTACT_CONSENT_STATUS_GRANTED
from marketing.models import MarketingCampaignMessage, MarketingCampaignSendRun
from marketing.services.campaigns.constants import ELIGIBILITY_ELIGIBLE, PURPOSE_TEST_CAMPAIGN
from marketing.services.campaigns.readiness import build_campaign_readiness
from marketing.services.campaigns.send_constants import (
    MESSAGE_STATUS_SENT,
    SEND_MODE_TEST,
    SEND_RUN_STATUS_RUNNING,
    SEND_RUN_TERMINAL_STATUSES,
    VARIABLE_KEY_REQUEST_HISTORY_URL,
)
from marketing.services.campaigns.send_settings import (
    MARKETING_TEST_SEND_MAX_RECIPIENTS,
    get_marketing_whatsapp_send_mode,
    marketing_test_whatsapp_send_enabled,
)
from marketing.services.campaigns.send_variables import (
    VariableResolutionError,
    resolve_template_variables_for_recipient,
)
from marketing.services.templates.constants import META_STATUS_APPROVED

if TYPE_CHECKING:
    from marketing.models import MarketingCampaign


class TestSendValidationError(Exception):
    pass


@dataclass(frozen=True)
class TestSendRecipientPreview:
    recipient_id: int
    masked_phone: str
    is_test_contact: bool
    consent_status: str
    consent_status_label: str
    variables: dict[str, str]
    ready: bool
    error_message: str = ''


@dataclass(frozen=True)
class TestSendPreflightResult:
    allowed: bool
    mode: str
    blocking_errors: tuple[str, ...]
    recipients: tuple[TestSendRecipientPreview, ...] = ()
    template_name: str = ''
    template_meta_name: str = ''
    language_code: str = ''
    campaign_name: str = ''
    recipient_count: int = 0
    already_sent: bool = False
    existing_run_id: int | None = None


def _eligible_test_recipients(campaign: MarketingCampaign):
    return list(
        campaign.recipients.filter(
            eligibility_status=ELIGIBILITY_ELIGIBLE,
            is_test_contact=True,
            consent_status=CONTACT_CONSENT_STATUS_GRANTED,
        ).order_by('id'),
    )


def get_eligible_test_recipients(campaign: MarketingCampaign):
    return _eligible_test_recipients(campaign)


def campaign_has_completed_test_send(campaign: MarketingCampaign) -> MarketingCampaignSendRun | None:
    return (
        MarketingCampaignSendRun.objects.filter(
            campaign=campaign,
            mode=SEND_MODE_TEST,
            status__in=SEND_RUN_TERMINAL_STATUSES,
        )
        .order_by('-created_at')
        .first()
    )


def campaign_has_sent_test_messages(campaign: MarketingCampaign) -> bool:
    return MarketingCampaignMessage.objects.filter(
        send_run__campaign=campaign,
        send_run__mode=SEND_MODE_TEST,
        status=MESSAGE_STATUS_SENT,
    ).exists()


def campaign_test_send_already_executed(campaign: MarketingCampaign) -> bool:
    if MarketingCampaignSendRun.objects.filter(
        campaign=campaign,
        mode=SEND_MODE_TEST,
        status=SEND_RUN_STATUS_RUNNING,
    ).exists():
        return True
    if campaign_has_sent_test_messages(campaign):
        return True
    return campaign_has_completed_test_send(campaign) is not None


def ensure_test_send_not_already_executed(campaign: MarketingCampaign) -> None:
    if MarketingCampaignSendRun.objects.filter(
        campaign=campaign,
        mode=SEND_MODE_TEST,
        status=SEND_RUN_STATUS_RUNNING,
    ).exists():
        raise TestSendValidationError('Отправка уже выполняется.')
    if campaign_has_sent_test_messages(campaign):
        raise TestSendValidationError('Тестовая отправка для этой кампании уже выполнялась.')
    if campaign_has_completed_test_send(campaign):
        raise TestSendValidationError('Тестовая отправка для этой кампании уже выполнялась.')


def build_test_send_preflight(campaign: MarketingCampaign) -> TestSendPreflightResult:
    mode = get_marketing_whatsapp_send_mode()
    blocking_errors: list[str] = []
    template = campaign.message_template

    if mode != SEND_MODE_TEST:
        blocking_errors.append('Отправка отключена настройками (MARKETING_WHATSAPP_SEND_MODE=OFF).')

    if campaign.purpose != PURPOSE_TEST_CAMPAIGN:
        blocking_errors.append('TEST-отправка разрешена только для test_campaign.')

    readiness = build_campaign_readiness(campaign)
    if not readiness['prepared_for_next_stage']:
        blocking_errors.append('Кампания не подготовлена к отправке.')
    if campaign.is_snapshot_stale():
        blocking_errors.append('Снимок получателей устарел.')
    if campaign.eligible_count <= 0:
        blocking_errors.append('Нет eligible получателей.')

    if not template:
        blocking_errors.append('Шаблон не выбран.')
    else:
        if not template.is_active:
            blocking_errors.append('Шаблон неактивен.')
        if template.meta_status != META_STATUS_APPROVED:
            blocking_errors.append('Шаблон не approved в Meta.')
        if not template.allow_test_campaign:
            blocking_errors.append('Шаблон не разрешён для test_campaign.')

    recipients = _eligible_test_recipients(campaign)
    if len(recipients) > MARKETING_TEST_SEND_MAX_RECIPIENTS:
        blocking_errors.append(
            f'Для TEST-отправки допустимо не более {MARKETING_TEST_SEND_MAX_RECIPIENTS} получателей.',
        )
    if not recipients and not blocking_errors:
        blocking_errors.append('Нет test contacts с consent Granted среди eligible получателей.')

    non_test = campaign.recipients.filter(
        eligibility_status=ELIGIBILITY_ELIGIBLE,
    ).exclude(is_test_contact=True).count()
    if non_test:
        blocking_errors.append('Среди eligible есть non-test получатели — TEST-отправка заблокирована.')

    bad_consent = campaign.recipients.filter(
        eligibility_status=ELIGIBILITY_ELIGIBLE,
    ).exclude(
        consent_status=CONTACT_CONSENT_STATUS_GRANTED,
    )
    if bad_consent.exists():
        blocking_errors.append('Не все eligible получатели имеют consent Granted.')

    existing_run = campaign_has_completed_test_send(campaign)
    if campaign_test_send_already_executed(campaign):
        blocking_errors.append('Тестовая отправка для этой кампании уже выполнялась.')

    recipient_previews: list[TestSendRecipientPreview] = []
    if template and recipients and not blocking_errors:
        for recipient in recipients:
            try:
                variables = resolve_template_variables_for_recipient(template, recipient)
            except VariableResolutionError as exc:
                blocking_errors.append(str(exc))
                recipient_previews.append(
                    TestSendRecipientPreview(
                        recipient_id=recipient.pk,
                        masked_phone=recipient.masked_phone,
                        is_test_contact=recipient.is_test_contact,
                        consent_status=recipient.consent_status,
                        consent_status_label=recipient.consent_status_label,
                        variables={},
                        ready=False,
                        error_message=str(exc),
                    ),
                )
                continue
            required_keys = {
                item['key']
                for item in (template.variables or [])
                if item.get('required')
            }
            missing = required_keys - set(variables.keys())
            if missing:
                blocking_errors.append(
                    f'Не заполнены обязательные переменные для {recipient.masked_phone}: '
                    f'{", ".join(sorted(missing))}.',
                )
            if VARIABLE_KEY_REQUEST_HISTORY_URL in required_keys:
                url = variables.get(VARIABLE_KEY_REQUEST_HISTORY_URL, '')
                if not url or '/my-requests/' not in url:
                    blocking_errors.append(
                        f'Некорректная request_history_url для {recipient.masked_phone}.',
                    )
            recipient_previews.append(
                TestSendRecipientPreview(
                    recipient_id=recipient.pk,
                    masked_phone=recipient.masked_phone,
                    is_test_contact=recipient.is_test_contact,
                    consent_status=recipient.consent_status,
                    consent_status_label=recipient.consent_status_label,
                    variables=variables,
                    ready=not missing,
                    error_message='',
                ),
            )

    allowed = marketing_test_whatsapp_send_enabled() and not blocking_errors
    return TestSendPreflightResult(
        allowed=allowed,
        mode=mode,
        blocking_errors=tuple(dict.fromkeys(blocking_errors)),
        recipients=tuple(recipient_previews),
        template_name=template.name if template else '',
        template_meta_name=template.meta_template_name if template else '',
        language_code=template.language_code if template else '',
        campaign_name=campaign.name,
        recipient_count=len(recipient_previews),
        already_sent=campaign_test_send_already_executed(campaign),
        existing_run_id=existing_run.pk if existing_run else None,
    )


def validate_test_send_executable(campaign: MarketingCampaign) -> TestSendPreflightResult:
    preflight = build_test_send_preflight(campaign)
    if not preflight.allowed:
        raise TestSendValidationError(
            preflight.blocking_errors[0] if preflight.blocking_errors else 'Отправка запрещена.',
        )
    return preflight
