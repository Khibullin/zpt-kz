from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from django.db.models import Exists, OuterRef

from core.models import (
    BUYER_BROADCAST_RECIPIENT_FAILED,
    BUYER_BROADCAST_STATUS_COMPLETED,
    BUYER_BROADCAST_STATUS_COMPLETED_WITH_ERRORS,
    BUYER_BROADCAST_STATUS_DRAFT,
    BUYER_BROADCAST_STATUS_QUEUED,
    BUYER_BROADCAST_STATUS_READY,
    BUYER_CONTACT_STATUS_UNSUBSCRIBED,
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    BuyerBroadcastCampaign,
    BuyerBroadcastRecipient,
    BuyerContact,
    ContactConsent,
)
from core.services.buyer_audience_service import eligible_buyer_contacts
from marketing.services.contacts import (
    ROLE_DETAILING,
    ROLE_MARKETPLACE_BUYER,
    ROLE_MARKETPLACE_SELLER,
    ROLE_PARTS_BUYER,
    ROLE_PARTS_SELLER,
    ROLE_SERVICE_CUSTOMER,
    ROLE_STO,
    MarketingContact,
    _has_combined_seller_roles,
    build_contact_registry,
)


@dataclass(frozen=True)
class DashboardOverviewStats:
    unique_phones: int
    marketing_available: int
    test_contacts: int
    marketing_granted: int
    marketing_unknown: int
    marketing_revoked: int
    unsubscribed: int
    draft_campaigns: int
    prepared_campaigns: int
    completed_campaigns: int
    send_errors: int


@dataclass(frozen=True)
class ContactGroupCard:
    key: str
    title: str
    section: str
    total: int
    active: int
    with_marketing_consent: int
    without_marketing_consent: int
    top_cities: tuple[tuple[str, int], ...]
    last_activity: datetime | None
    note: str = ''


def _marketing_consent_exists(**status_kwargs) -> Exists:
    return Exists(
        ContactConsent.objects.filter(
            buyer=OuterRef('pk'),
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            **status_kwargs,
        ),
    )


def get_overview_stats() -> DashboardOverviewStats:
    registry = build_contact_registry()
    marketing_granted_q = _marketing_consent_exists(status=CONTACT_CONSENT_STATUS_GRANTED)
    marketing_available = eligible_buyer_contacts().filter(marketing_granted_q).count()

    marketing_consents = ContactConsent.objects.filter(
        channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
        purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
    )
    granted_buyer_ids = set(
        marketing_consents.filter(status=CONTACT_CONSENT_STATUS_GRANTED).values_list(
            'buyer_id',
            flat=True,
        ),
    )
    unknown_buyer_ids = set(
        marketing_consents.filter(status=CONTACT_CONSENT_STATUS_UNKNOWN).values_list(
            'buyer_id',
            flat=True,
        ),
    )
    revoked_buyer_ids = set(
        marketing_consents.filter(status=CONTACT_CONSENT_STATUS_REVOKED).values_list(
            'buyer_id',
            flat=True,
        ),
    )
    buyers_with_consent = granted_buyer_ids | unknown_buyer_ids | revoked_buyer_ids
    all_buyer_ids = set(BuyerContact.objects.values_list('pk', flat=True))
    missing_count = len(all_buyer_ids - buyers_with_consent)

    return DashboardOverviewStats(
        unique_phones=len(registry),
        marketing_available=marketing_available,
        test_contacts=BuyerContact.objects.filter(is_test_contact=True).count(),
        marketing_granted=len(granted_buyer_ids),
        marketing_unknown=len(unknown_buyer_ids) + missing_count,
        marketing_revoked=len(revoked_buyer_ids),
        unsubscribed=BuyerContact.objects.filter(
            status=BUYER_CONTACT_STATUS_UNSUBSCRIBED,
        ).count(),
        draft_campaigns=BuyerBroadcastCampaign.objects.filter(
            status=BUYER_BROADCAST_STATUS_DRAFT,
        ).count(),
        prepared_campaigns=BuyerBroadcastCampaign.objects.filter(
            status__in=[BUYER_BROADCAST_STATUS_READY, BUYER_BROADCAST_STATUS_QUEUED],
        ).count(),
        completed_campaigns=BuyerBroadcastCampaign.objects.filter(
            status__in=[
                BUYER_BROADCAST_STATUS_COMPLETED,
                BUYER_BROADCAST_STATUS_COMPLETED_WITH_ERRORS,
            ],
        ).count(),
        send_errors=BuyerBroadcastRecipient.objects.filter(
            status=BUYER_BROADCAST_RECIPIENT_FAILED,
        ).count(),
    )


def _card_from_contacts(
    *,
    key: str,
    title: str,
    section: str,
    contacts: list[MarketingContact],
    note: str = '',
) -> ContactGroupCard:
    total = len(contacts)
    active = sum(1 for contact in contacts if contact.is_active)
    with_consent = sum(
        1
        for contact in contacts
        if contact.marketing_consent == CONTACT_CONSENT_STATUS_GRANTED
    )
    without_consent = total - with_consent
    city_counter = Counter(
        contact.city for contact in contacts if contact.city.strip()
    )
    last_activity = None
    for contact in contacts:
        if contact.last_activity and (
            last_activity is None or contact.last_activity > last_activity
        ):
            last_activity = contact.last_activity
    return ContactGroupCard(
        key=key,
        title=title,
        section=section,
        total=total,
        active=active,
        with_marketing_consent=with_consent,
        without_marketing_consent=without_consent,
        top_cities=tuple(city_counter.most_common(5)),
        last_activity=last_activity,
        note=note,
    )


def get_group_cards() -> list[ContactGroupCard]:
    contacts = list(build_contact_registry().values())

    marketplace_buyers = [
        contact for contact in contacts if ROLE_MARKETPLACE_BUYER in contact.roles
    ]
    marketplace_note = ''
    if not marketplace_buyers:
        marketplace_note = (
            'Данные появятся после начала оформления покупок через маркетплейс'
        )

    return [
        _card_from_contacts(
            key='parts_buyers',
            title='Покупатели по заявкам на запчасти',
            section='buyers',
            contacts=[c for c in contacts if ROLE_PARTS_BUYER in c.roles],
        ),
        _card_from_contacts(
            key='marketplace_buyers',
            title='Покупатели товаров маркетплейса',
            section='buyers',
            contacts=marketplace_buyers,
            note=marketplace_note,
        ),
        _card_from_contacts(
            key='service_customers',
            title='Заказчики услуг СТО и детейлинга',
            section='buyers',
            contacts=[c for c in contacts if ROLE_SERVICE_CUSTOMER in c.roles],
        ),
        _card_from_contacts(
            key='parts_sellers',
            title='Получают заявки покупателей',
            section='sellers',
            contacts=[c for c in contacts if ROLE_PARTS_SELLER in c.roles],
        ),
        _card_from_contacts(
            key='marketplace_sellers',
            title='Размещают товары в маркетплейсе',
            section='sellers',
            contacts=[c for c in contacts if ROLE_MARKETPLACE_SELLER in c.roles],
        ),
        _card_from_contacts(
            key='combined_sellers',
            title='Совмещают оба направления',
            section='sellers',
            contacts=[c for c in contacts if _has_combined_seller_roles(c)],
        ),
        _card_from_contacts(
            key='sto',
            title='Исполнители СТО',
            section='executors',
            contacts=[c for c in contacts if ROLE_STO in c.roles],
        ),
        _card_from_contacts(
            key='detailing',
            title='Исполнители детейлинга',
            section='executors',
            contacts=[c for c in contacts if ROLE_DETAILING in c.roles],
        ),
        _card_from_contacts(
            key='test_contacts',
            title='Тестовые контакты',
            section='extra',
            contacts=[c for c in contacts if c.is_test],
        ),
    ]
