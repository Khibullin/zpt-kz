from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError

from marketing.services.campaigns.compatibility import (
    is_audience_compatible_with_purpose,
    is_purpose_audience_compatible,
)
from marketing.services.campaigns.constants import (
    STATUS_ARCHIVED,
    STATUS_CANCELLED,
    STATUS_DRAFT,
    STATUS_AUDIENCE_PREPARED,
)
from marketing.services.templates.selectors import (
    template_is_compatible_with_campaign,
)

if TYPE_CHECKING:
    from marketing.models import MarketingAudience, MarketingCampaign


class CampaignValidationError(Exception):
    pass


def validate_campaign_form_fields(
    *,
    name: str,
    purpose: str,
    audience: MarketingAudience | None,
    audience_id: str,
    message_template=None,
) -> None:
    if not name.strip():
        raise CampaignValidationError('Укажите название кампании.')
    if not purpose:
        raise CampaignValidationError('Выберите назначение кампании.')
    if not audience_id:
        raise CampaignValidationError('Выберите сохранённую аудиторию.')
    if audience is None:
        raise CampaignValidationError('Выбранная аудитория не найдена.')
    if not audience.is_active:
        raise CampaignValidationError('Выбранная аудитория неактивна.')
    if not is_audience_compatible_with_purpose(audience, purpose):
        raise CampaignValidationError(
            'Выбранная аудитория несовместима с назначением кампании.',
        )
    if message_template is not None and not template_is_compatible_with_campaign(
        message_template,
        purpose=purpose,
    ):
        raise CampaignValidationError(
            'Выбранный шаблон недоступен для назначения кампании.',
        )


def resolve_audience_from_post(audience_id: str, *, purpose: str) -> MarketingAudience:
    from marketing.models import MarketingAudience

    if not audience_id:
        raise CampaignValidationError('Выберите сохранённую аудиторию.')
    try:
        audience = MarketingAudience.objects.get(pk=int(audience_id))
    except (MarketingAudience.DoesNotExist, ValueError, TypeError) as exc:
        raise CampaignValidationError('Выбранная аудитория не найдена.') from exc
    if not is_audience_compatible_with_purpose(audience, purpose):
        raise CampaignValidationError(
            'Выбранная аудитория несовместима с назначением кампании.',
        )
    return audience


def validate_campaign_editable(campaign: MarketingCampaign) -> None:
    if campaign.status in {STATUS_CANCELLED, STATUS_ARCHIVED}:
        raise CampaignValidationError('Кампания не может быть изменена в текущем статусе.')


def validate_campaign_preparable(campaign: MarketingCampaign) -> None:
    if campaign.status in {STATUS_CANCELLED, STATUS_ARCHIVED}:
        raise CampaignValidationError('Кампания не может быть подготовлена в текущем статусе.')
    if not campaign.audience.is_active:
        raise CampaignValidationError('Аудитория кампании неактивна.')
    if not is_audience_compatible_with_purpose(campaign.audience, campaign.purpose):
        raise CampaignValidationError(
            'Аудитория кампании несовместима с её назначением.',
        )


def validate_campaign_deletable(campaign: MarketingCampaign) -> None:
    if campaign.send_runs.exists():
        raise CampaignValidationError(
            'Кампания имеет историю отправок и не может быть удалена. Используйте архив.',
        )


def campaign_model_clean(campaign: MarketingCampaign) -> None:
    if not campaign.name.strip():
        raise ValidationError({'name': 'Укажите название кампании.'})
    if campaign.audience_id and campaign.purpose:
        if not is_audience_compatible_with_purpose(campaign.audience, campaign.purpose):
            raise ValidationError({
                'audience': 'Аудитория несовместима с назначением кампании.',
            })
