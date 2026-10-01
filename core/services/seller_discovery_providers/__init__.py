"""Provider layer for Seller Discovery.

Clients return SellerDiscoveryHit values. They do not create SellerLead rows.
"""

from core.services.seller_discovery_providers.base import (
    DiscoveryProviderConfigError,
    DiscoveryProviderError,
    SellerDiscoveryHit,
    build_discovery_provider,
)

__all__ = [
    'DiscoveryProviderConfigError',
    'DiscoveryProviderError',
    'SellerDiscoveryHit',
    'build_discovery_provider',
]
