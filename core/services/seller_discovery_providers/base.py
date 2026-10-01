"""Shared discovery hit and provider errors.

A hit is the normalized observation a provider returns. Ingestion is the only
layer that writes SellerLead rows.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol


class DiscoveryProviderError(Exception):
    """Ошибка поиска в источнике."""


class DiscoveryProviderConfigError(DiscoveryProviderError):
    """Источник не настроен: нет ключа или флаг discovery выключен."""


def require_discovery_provider(provider: str) -> None:
    """A provider may call the network only when master and its own flag are on."""
    from django.conf import settings

    master = bool(getattr(settings, 'SELLER_DISCOVERY_ENABLED', False))
    if not master:
        raise DiscoveryProviderConfigError(
            'SELLER_DISCOVERY_ENABLED=False. Реальные запросы discovery отключены.',
        )
    key = str(provider or '').strip().lower().replace('-', '_')
    if key in {'two_gis', '2gis'}:
        if not bool(getattr(settings, 'SELLER_DISCOVERY_2GIS_ENABLED', False)):
            raise DiscoveryProviderConfigError(
                'SELLER_DISCOVERY_2GIS_ENABLED=False. Запросы к 2GIS отключены.',
            )
        return
    if key in {'brave', 'brave_search'}:
        if not bool(getattr(settings, 'SELLER_DISCOVERY_BRAVE_WEB_ENABLED', False)):
            raise DiscoveryProviderConfigError(
                'SELLER_DISCOVERY_BRAVE_WEB_ENABLED=False. Web discovery Brave отключён.',
            )
        return
    raise DiscoveryProviderConfigError(f'Неизвестный источник discovery: {provider}')


@dataclass(frozen=True)
class SellerDiscoveryHit:
    provider: str
    source_type: str
    external_id: str
    name: str
    city: str = ''
    address: str = ''
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    phone: str = ''
    phones: tuple[str, ...] = ()
    whatsapp_phone: str = ''
    website: str = ''
    instagram_url: str = ''
    category_text: str = ''
    rubrics: tuple[str, ...] = ()
    source_url: str = ''
    search_query: str = ''
    org_id: str = ''
    brand_name: str = ''
    confidence: int = 50
    raw_data: dict[str, Any] = field(default_factory=dict)
    observed_at: datetime | None = None


def payload_hash(data: dict) -> str:
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


class DiscoveryProvider(Protocol):
    name: str

    def search(
        self,
        *,
        city: str,
        direction: str,
        limit: int | None = None,
        max_pages: int | None = None,
    ) -> list[SellerDiscoveryHit]:
        """Return normalized hits. Must not write to the database."""


def build_discovery_provider(name: str) -> DiscoveryProvider:
    key = str(name or '').strip().lower().replace('-', '_')
    if key in {'two_gis', '2gis'}:
        from core.services.seller_discovery_providers.two_gis import TwoGisDiscoveryProvider

        return TwoGisDiscoveryProvider()
    if key in {'brave', 'brave_search'}:
        from core.services.seller_discovery_providers.brave import BraveWebDiscoveryProvider

        return BraveWebDiscoveryProvider()
    raise DiscoveryProviderConfigError(f'Неизвестный источник discovery: {name}')
