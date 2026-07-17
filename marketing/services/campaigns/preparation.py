from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from marketing.models import MarketingCampaign, MarketingCampaignRecipient
from marketing.services.audiences.calculators import collect_audience_snapshot
from marketing.services.campaigns.constants import (
    STATUS_AUDIENCE_PREPARED,
    STATUS_DRAFT,
)
from marketing.services.campaigns.signatures import compute_audience_signature
from marketing.services.campaigns.validation import (
    CampaignValidationError,
    validate_campaign_preparable,
)

BULK_CREATE_BATCH_SIZE = 500


def _reset_campaign_counts(campaign: MarketingCampaign) -> None:
    campaign.matched_count = 0
    campaign.unique_count = 0
    campaign.eligible_count = 0
    campaign.excluded_count = 0
    campaign.test_count = 0
    campaign.invalid_phone_count = 0
    campaign.duplicate_count = 0
    campaign.inactive_count = 0
    campaign.consent_granted_count = 0
    campaign.consent_unknown_count = 0
    campaign.consent_revoked_count = 0
    campaign.consent_not_recorded_count = 0
    campaign.audience_prepared_at = None
    campaign.audience_updated_at_at_prepare = None
    campaign.audience_signature_at_prepare = ''


def clear_campaign_snapshot(campaign: MarketingCampaign) -> None:
    campaign.recipients.all().delete()
    _reset_campaign_counts(campaign)
    campaign.status = STATUS_DRAFT


def prepare_campaign_snapshot(campaign_id: int) -> MarketingCampaign:
    with transaction.atomic():
        campaign = (
            MarketingCampaign.objects.select_for_update()
            .select_related('audience')
            .get(pk=campaign_id)
        )
        validate_campaign_preparable(campaign)

        audience = campaign.audience
        snapshot = collect_audience_snapshot(
            contact_group=audience.contact_group,
            contact_subtype=audience.contact_subtype,
            criteria=audience.criteria,
            purpose=campaign.purpose,
        )

        audience.last_calculated_at = timezone.now()
        audience.last_matched_count = snapshot.calculation.matched_count
        audience.last_eligible_count = snapshot.calculation.eligible_count
        audience.save(
            update_fields=[
                'last_calculated_at',
                'last_matched_count',
                'last_eligible_count',
            ],
        )

        campaign.recipients.all().delete()
        recipient_rows = [
            MarketingCampaignRecipient(
                campaign=campaign,
                phone_normalized=row.phone_normalized,
                display_name=row.display_name,
                city=row.city,
                roles=row.roles,
                vehicle_summary=row.vehicle_summary,
                last_activity_at=row.last_activity_at,
                is_test_contact=row.is_test_contact,
                consent_status=row.consent_status,
                eligibility_status=row.eligibility_status,
                exclusion_reason=row.exclusion_reason,
                source_summary=row.source_summary,
            )
            for row in snapshot.contacts
        ]
        if recipient_rows:
            MarketingCampaignRecipient.objects.bulk_create(
                recipient_rows,
                batch_size=BULK_CREATE_BATCH_SIZE,
            )

        campaign.matched_count = snapshot.matched_count
        campaign.unique_count = snapshot.unique_count
        campaign.eligible_count = snapshot.eligible_count
        campaign.excluded_count = snapshot.excluded_count
        campaign.test_count = snapshot.test_count
        campaign.invalid_phone_count = snapshot.invalid_phone_count
        campaign.duplicate_count = snapshot.duplicate_count
        campaign.inactive_count = snapshot.inactive_count
        campaign.consent_granted_count = snapshot.consent_granted_count
        campaign.consent_unknown_count = snapshot.consent_unknown_count
        campaign.consent_revoked_count = snapshot.consent_revoked_count
        campaign.consent_not_recorded_count = snapshot.consent_not_recorded_count
        campaign.audience_prepared_at = timezone.now()
        campaign.audience_updated_at_at_prepare = audience.updated_at
        campaign.audience_signature_at_prepare = compute_audience_signature(audience)
        campaign.status = STATUS_AUDIENCE_PREPARED
        campaign.save()
        return campaign


def copy_campaign(source: MarketingCampaign, *, created_by) -> MarketingCampaign:
    copy_name = f'Копия — {source.name}'
    suffix = 2
    while MarketingCampaign.objects.filter(name=copy_name).exists():
        copy_name = f'Копия — {source.name} ({suffix})'
        suffix += 1
    return MarketingCampaign.objects.create(
        name=copy_name,
        description=source.description,
        audience=source.audience,
        purpose=source.purpose,
        channel=source.channel,
        status=STATUS_DRAFT,
        is_active=source.is_active,
        created_by=created_by,
    )
