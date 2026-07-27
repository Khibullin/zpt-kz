from __future__ import annotations

from marketing.services.campaigns.constants import (
    PURPOSE_MARKETPLACE_BUYERS,
    PURPOSE_PARTS_BUYERS,
    PURPOSE_REQUEST_SELLERS,
)
from marketing.services.simple_mailing.constants import (
    RECIPIENT_TYPE_MARKETPLACE_BUYERS,
    RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
    RECIPIENT_TYPE_SELLERS,
)

RECIPIENT_TYPE_TO_CAMPAIGN_PURPOSE = {
    RECIPIENT_TYPE_PARTS_REQUEST_BUYERS: PURPOSE_PARTS_BUYERS,
    RECIPIENT_TYPE_MARKETPLACE_BUYERS: PURPOSE_MARKETPLACE_BUYERS,
    RECIPIENT_TYPE_SELLERS: PURPOSE_REQUEST_SELLERS,
}


def recipient_type_to_campaign_purpose(recipient_type: str) -> str:
    try:
        return RECIPIENT_TYPE_TO_CAMPAIGN_PURPOSE[recipient_type]
    except KeyError as exc:
        raise ValueError(f'Unknown recipient type: {recipient_type}') from exc
