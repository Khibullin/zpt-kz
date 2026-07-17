from __future__ import annotations

from marketing.services.campaigns.constants import (
    EXCLUSION_AUDIENCE_RULE,
    PURPOSE_COMBINED_SELLERS,
    PURPOSE_DETAILING_PROVIDERS,
    PURPOSE_MARKETPLACE_BUYERS,
    PURPOSE_MARKETPLACE_SELLERS,
    PURPOSE_PARTS_BUYERS,
    PURPOSE_REQUEST_SELLERS,
    PURPOSE_SERVICE_CUSTOMERS,
    PURPOSE_STO_PROVIDERS,
    PURPOSE_TEST_CAMPAIGN,
)
from marketing.services.contacts import (
    ROLE_DETAILING,
    ROLE_MARKETPLACE_BUYER,
    ROLE_MARKETPLACE_SELLER,
    ROLE_PARTS_BUYER,
    ROLE_PARTS_SELLER,
    ROLE_SERVICE_CUSTOMER,
    ROLE_STO,
    MarketingContact,
)


def contact_matches_campaign_purpose(
    contact: MarketingContact,
    purpose: str,
    *,
    test_marketplace_keys: frozenset[str],
) -> bool:
    if purpose == PURPOSE_PARTS_BUYERS:
        return ROLE_PARTS_BUYER in contact.roles
    if purpose == PURPOSE_MARKETPLACE_BUYERS:
        return (
            ROLE_MARKETPLACE_BUYER in contact.roles
            and contact.phone_key not in test_marketplace_keys
            and not contact.is_test
        )
    if purpose == PURPOSE_SERVICE_CUSTOMERS:
        return ROLE_SERVICE_CUSTOMER in contact.roles
    if purpose == PURPOSE_REQUEST_SELLERS:
        return ROLE_PARTS_SELLER in contact.roles
    if purpose == PURPOSE_MARKETPLACE_SELLERS:
        return ROLE_MARKETPLACE_SELLER in contact.roles
    if purpose == PURPOSE_COMBINED_SELLERS:
        return ROLE_PARTS_SELLER in contact.roles and ROLE_MARKETPLACE_SELLER in contact.roles
    if purpose == PURPOSE_STO_PROVIDERS:
        return ROLE_STO in contact.roles
    if purpose == PURPOSE_DETAILING_PROVIDERS:
        return ROLE_DETAILING in contact.roles
    if purpose == PURPOSE_TEST_CAMPAIGN:
        return contact.is_test
    return False


def apply_purpose_to_snapshot_status(
    contact: MarketingContact,
    purpose: str,
    *,
    test_marketplace_keys: frozenset[str],
    eligibility_status: str,
    exclusion_reason: str,
) -> tuple[str, str]:
    if contact_matches_campaign_purpose(
        contact,
        purpose,
        test_marketplace_keys=test_marketplace_keys,
    ):
        return eligibility_status, exclusion_reason
    return 'excluded', EXCLUSION_AUDIENCE_RULE
