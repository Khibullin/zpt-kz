"""What each contact source is allowed to contribute.

This is the capability matrix for Seller Contact Enrichment. It does not
perform network calls. Kolesa stays disabled until there is written permission
or an official API.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceCapability:
    name: str
    locator: bool
    whatsapp_evidence: str
    phone_evidence: str
    persistent_contact_data: bool
    enabled: bool = True
    reason: str = ''


SOURCE_CAPABILITIES: dict[str, SourceCapability] = {
    'website': SourceCapability(
        name='website',
        locator=False,
        whatsapp_evidence='verified',
        phone_evidence='verified',
        persistent_contact_data=True,
    ),
    'two_gis': SourceCapability(
        name='two_gis',
        locator=True,
        whatsapp_evidence='only_explicit_contact',
        phone_evidence='verified',
        persistent_contact_data=True,
    ),
    'google_places': SourceCapability(
        name='google_places',
        locator=True,
        whatsapp_evidence='false',
        phone_evidence='false',
        persistent_contact_data=False,
    ),
    'brave': SourceCapability(
        name='brave',
        locator=True,
        whatsapp_evidence='candidate',
        phone_evidence='false',
        persistent_contact_data=False,
    ),
    'yandex_org': SourceCapability(
        name='yandex_org',
        locator=True,
        whatsapp_evidence='false',
        phone_evidence='false',
        persistent_contact_data=False,
    ),
    'kolesa': SourceCapability(
        name='kolesa',
        locator=False,
        whatsapp_evidence='false',
        phone_evidence='false',
        persistent_contact_data=False,
        enabled=False,
        reason='permission_required',
    ),
}


def source_capability(name: str) -> SourceCapability | None:
    return SOURCE_CAPABILITIES.get(name)
