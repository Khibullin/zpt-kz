from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.models import CONTACT_CONSENT_STATUS_GRANTED, CONTACT_CONSENT_STATUS_REVOKED, CONTACT_CONSENT_STATUS_UNKNOWN
from marketing.models import MarketingCampaignMessage, MarketingCampaignSendRun
from marketing.services.campaigns.constants import ELIGIBILITY_ELIGIBLE, PURPOSE_TEST_CAMPAIGN
from marketing.services.campaigns.live_consent import (
    SKIP_REASON_CONSENT_NOT_GRANTED,
    SKIP_REASON_CONSENT_REVOKED,
    SKIP_REASON_CONSENT_UNKNOWN,
    SKIP_REASON_INACTIVE,
    SKIP_REASON_INVALID_PHONE,
    SKIP_REASON_PURPOSE_MISMATCH,
    SKIP_REASON_TEST_CONTACT,
    evaluate_live_recipient_from_snapshot,
    recheck_live_recipient_consent,
)
from marketing.services.campaigns.readiness import build_campaign_readiness
from marketing.services.campaigns.send_constants import (
    MESSAGE_STATUS_LIVE_DELIVERED,
    MESSAGE_STATUS_LIVE_IN_FLIGHT,
    MESSAGE_STATUS_SENT,
    SEND_MODE_LIVE,
    SEND_RUN_LIVE_LAUNCH_BLOCK_STATUSES,
    VARIABLE_KEY_REQUEST_HISTORY_URL,
)
from marketing.services.campaigns.send_settings import (
    get_marketing_live_batch_size,
    get_marketing_live_max_recipients,
    get_marketing_whatsapp_send_mode,
    marketing_live_whatsapp_send_enabled,
)
from marketing.services.campaigns.send_variables import (
    VariableResolutionError,
    resolve_template_variables_for_recipient,
)
from marketing.services.templates.constants import META_STATUS_APPROVED

if TYPE_CHECKING:
    from marketing.models import MarketingCampaign, MarketingCampaignRecipient


class LiveSendValidationError(Exception):
    pass


@dataclass(frozen=True)
class LiveSendRecipientPreview:
    recipient_id: int
    masked_phone: str
    consent_status: str
    consent_status_label: str
    url_resolved: bool
    eligible_now: bool
    exclusion_reason: str = ''


@dataclass
class LiveSendPreflightResult:
    allowed: bool
    mode: str
    blocking_errors: tuple[str, ...] = ()
    recipients: tuple[LiveSendRecipientPreview, ...] = ()
    template_name: str = ''
    template_meta_name: str = ''
    language_code: str = ''
    campaign_name: str = ''
    purpose_label: str = ''
    audience_name: str = ''
    matched_count: int = 0
    snapshot_eligible_count: int = 0
    eligible_now_count: int = 0
    excluded_count: int = 0
    exclusion_breakdown: dict[str, int] = field(default_factory=dict)
    batch_limit: int = 0
    max_recipients: int = 0
    already_launched: bool = False
    existing_run_id: int | None = None


def campaign_has_blocking_live_run(campaign: MarketingCampaign) -> MarketingCampaignSendRun | None:
    return (
        MarketingCampaignSendRun.objects.filter(
            campaign=campaign,
            mode=SEND_MODE_LIVE,
            status__in=SEND_RUN_LIVE_LAUNCH_BLOCK_STATUSES,
        )
        .order_by('-created_at')
        .first()
    )


def campaign_recipient_already_live_sent(campaign_id: int, recipient_id: int) -> bool:
    return MarketingCampaignMessage.objects.filter(
        send_run__campaign_id=campaign_id,
        send_run__mode=SEND_MODE_LIVE,
        campaign_recipient_id=recipient_id,
        status__in=MESSAGE_STATUS_LIVE_DELIVERED | {MESSAGE_STATUS_SENT},
    ).exists()


def campaign_recipient_live_in_flight(campaign_id: int, recipient_id: int) -> bool:
    return MarketingCampaignMessage.objects.filter(
        send_run__campaign_id=campaign_id,
        send_run__mode=SEND_MODE_LIVE,
        campaign_recipient_id=recipient_id,
        status__in=MESSAGE_STATUS_LIVE_IN_FLIGHT,
    ).exists()


def _snapshot_exclusion_reason(recipient: MarketingCampaignRecipient) -> str:
    if recipient.is_test_contact:
        return SKIP_REASON_TEST_CONTACT
    if recipient.eligibility_status != ELIGIBILITY_ELIGIBLE:
        if recipient.exclusion_reason == 'invalid_phone':
            return SKIP_REASON_INVALID_PHONE
        if recipient.exclusion_reason == 'inactive':
            return SKIP_REASON_INACTIVE
        if recipient.exclusion_reason in {'consent_revoked'}:
            return SKIP_REASON_CONSENT_REVOKED
        if recipient.exclusion_reason in {'consent_unknown', 'consent_not_recorded'}:
            return SKIP_REASON_CONSENT_UNKNOWN if recipient.exclusion_reason == 'consent_unknown' else SKIP_REASON_CONSENT_NOT_GRANTED
        return SKIP_REASON_PURPOSE_MISMATCH
    if recipient.consent_status == CONTACT_CONSENT_STATUS_REVOKED:
        return SKIP_REASON_CONSENT_REVOKED
    if recipient.consent_status == CONTACT_CONSENT_STATUS_UNKNOWN:
        return SKIP_REASON_CONSENT_UNKNOWN
    if recipient.consent_status != CONTACT_CONSENT_STATUS_GRANTED:
        return SKIP_REASON_CONSENT_NOT_GRANTED
    return ''


def build_live_send_preflight(campaign: MarketingCampaign) -> LiveSendPreflightResult:
    mode = get_marketing_whatsapp_send_mode()
    blocking_errors: list[str] = []
    template = campaign.message_template
    max_recipients = get_marketing_live_max_recipients()
    batch_limit = get_marketing_live_batch_size()

    if mode != SEND_MODE_LIVE:
        blocking_errors.append('LIVE-отправка отключена настройками (MARKETING_WHATSAPP_SEND_MODE≠LIVE).')

    if campaign.purpose == PURPOSE_TEST_CAMPAIGN:
        blocking_errors.append('LIVE-отправка запрещена для test_campaign.')

    readiness = build_campaign_readiness(campaign)
    if not readiness['prepared_for_next_stage']:
        blocking_errors.append('Кампания не подготовлена к LIVE-отправке.')
    if campaign.is_snapshot_stale():
        blocking_errors.append('Снимок получателей устарел.')
    if not campaign.has_prepared_snapshot:
        blocking_errors.append('Снимок получателей не подготовлен.')

    if not template:
        blocking_errors.append('Шаблон не выбран.')
    elif template:
        if not template.is_active:
            blocking_errors.append('Шаблон неактивен.')
        if template.meta_status != META_STATUS_APPROVED:
            blocking_errors.append('Шаблон не approved в Meta.')
        if not template.allows_campaign_purpose(campaign.purpose):
            blocking_errors.append('Шаблон несовместим с назначением кампании.')

    existing_run = campaign_has_blocking_live_run(campaign)
    if existing_run:
        blocking_errors.append('LIVE-отправка для этой кампании уже запущена или завершена.')

    snapshot_recipients = list(campaign.recipients.all().order_by('id'))
    exclusion_breakdown: dict[str, int] = {}
    eligible_now_previews: list[LiveSendRecipientPreview] = []

    for recipient in snapshot_recipients:
        snapshot_ok, snapshot_reason = evaluate_live_recipient_from_snapshot(recipient)
        if not snapshot_ok:
            exclusion_breakdown[snapshot_reason] = exclusion_breakdown.get(snapshot_reason, 0) + 1
            continue

        live_ok, live_reason = recheck_live_recipient_consent(recipient)
        if not live_ok:
            exclusion_breakdown[live_reason] = exclusion_breakdown.get(live_reason, 0) + 1
            continue

        if campaign_recipient_already_live_sent(campaign.pk, recipient.pk):
            exclusion_breakdown['already_sent'] = exclusion_breakdown.get('already_sent', 0) + 1
            continue
        if campaign_recipient_live_in_flight(campaign.pk, recipient.pk):
            exclusion_breakdown['already_queued'] = exclusion_breakdown.get('already_queued', 0) + 1
            continue

        url_resolved = False
        if template:
            try:
                variables = resolve_template_variables_for_recipient(template, recipient)
                url_resolved = bool(variables.get(VARIABLE_KEY_REQUEST_HISTORY_URL))
            except VariableResolutionError:
                exclusion_breakdown['missing_variable'] = exclusion_breakdown.get('missing_variable', 0) + 1
                continue

        eligible_now_previews.append(
            LiveSendRecipientPreview(
                recipient_id=recipient.pk,
                masked_phone=recipient.masked_phone,
                consent_status=recipient.consent_status,
                consent_status_label=recipient.consent_status_label,
                url_resolved=url_resolved,
                eligible_now=True,
            ),
        )

    eligible_now_count = len(eligible_now_previews)
    snapshot_eligible = campaign.eligible_count
    excluded_count = max(0, campaign.matched_count - campaign.eligible_count)

    if eligible_now_count <= 0 and not blocking_errors:
        blocking_errors.append('Нет получателей, допустимых для LIVE-отправки прямо сейчас.')

    if snapshot_eligible > max_recipients:
        blocking_errors.append(
            f'В аудитории {snapshot_eligible} допустимых получателей по snapshot, '
            f'а текущий лимит LIVE — {max_recipients}. '
            'Создайте/сузьте аудиторию или увеличьте контролируемый лимит.',
        )

    if eligible_now_count > max_recipients:
        blocking_errors.append(
            f'Допустимо для LIVE прямо сейчас: {eligible_now_count}, '
            f'лимит LIVE — {max_recipients}.',
        )

    allowed = marketing_live_whatsapp_send_enabled() and not blocking_errors and eligible_now_count > 0

    return LiveSendPreflightResult(
        allowed=allowed,
        mode=mode,
        blocking_errors=tuple(dict.fromkeys(blocking_errors)),
        recipients=tuple(eligible_now_previews[:max_recipients]),
        template_name=template.name if template else '',
        template_meta_name=template.meta_template_name if template else '',
        language_code=template.language_code if template else '',
        campaign_name=campaign.name,
        purpose_label=campaign.purpose_label,
        audience_name=campaign.audience.name,
        matched_count=campaign.matched_count if campaign.has_prepared_snapshot else 0,
        snapshot_eligible_count=snapshot_eligible,
        eligible_now_count=eligible_now_count,
        excluded_count=excluded_count,
        exclusion_breakdown=exclusion_breakdown,
        batch_limit=batch_limit,
        max_recipients=max_recipients,
        already_launched=existing_run is not None,
        existing_run_id=existing_run.pk if existing_run else None,
    )


def validate_live_send_executable(campaign: MarketingCampaign) -> LiveSendPreflightResult:
    preflight = build_live_send_preflight(campaign)
    if not preflight.allowed:
        raise LiveSendValidationError(
            preflight.blocking_errors[0] if preflight.blocking_errors else 'LIVE-отправка запрещена.',
        )
    return preflight


def validate_live_send_confirmation(campaign: MarketingCampaign, confirmation_text: str) -> None:
    normalized = (confirmation_text or '').strip()
    if normalized not in {'LIVE', campaign.name.strip()}:
        raise LiveSendValidationError(
            'Для подтверждения LIVE-отправки введите LIVE или точное название кампании.',
        )
