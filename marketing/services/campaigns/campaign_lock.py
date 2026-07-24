from __future__ import annotations

from marketing.models import MarketingCampaign


def lock_campaign_for_send(campaign_id: int) -> MarketingCampaign:
    """Lock campaign row only; load nullable FKs via separate queries."""
    return MarketingCampaign.objects.select_for_update().get(pk=campaign_id)
