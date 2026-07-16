from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.db.models import Count, Max, Prefetch
from django.utils import timezone

from catalog.models import Product, SellerProfile
from core.buyer_contact_admin_filters import marketing_consent_label
from core.models import (
    BUYER_CONTACT_STATUS_ACTIVE,
    BUYER_CONTACT_STATUS_UNSUBSCRIBED,
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    BuyerCategoryInterest,
    BuyerContact,
    ContactConsent,
    Seller,
)
from core.services.buyer_contact_utils import mask_phone, normalize_buyer_text
from marketing.services.phone_utils import normalize_phone_key
from marketing.services.marketplace_orders import (
    get_test_marketplace_phone_keys,
    iter_marketplace_order_phone_stats,
)
from service_requests.models import ServiceRequest, ServiceSeller

ROLE_PARTS_BUYER = 'parts_buyer'
ROLE_MARKETPLACE_BUYER = 'marketplace_buyer'
ROLE_SERVICE_CUSTOMER = 'service_customer'
ROLE_PARTS_SELLER = 'parts_seller'
ROLE_MARKETPLACE_SELLER = 'marketplace_seller'
ROLE_STO = 'sto'
ROLE_DETAILING = 'detailing'

ROLE_LABELS = {
    ROLE_PARTS_BUYER: 'Покупатель по заявкам',
    ROLE_MARKETPLACE_BUYER: 'Покупатель товаров',
    ROLE_SERVICE_CUSTOMER: 'Заказчик услуг',
    ROLE_PARTS_SELLER: 'Продавец заявок',
    ROLE_MARKETPLACE_SELLER: 'Продавец маркетплейса',
    ROLE_STO: 'Исполнитель СТО',
    ROLE_DETAILING: 'Исполнитель детейлинга',
}

TAB_ALL = 'all'
TAB_PARTS_BUYERS = 'parts_buyers'
TAB_MARKETPLACE_BUYERS = 'marketplace_buyers'
TAB_SERVICE_CUSTOMERS = 'service_customers'
TAB_PARTS_SELLERS = 'parts_sellers'
TAB_MARKETPLACE_SELLERS = 'marketplace_sellers'
TAB_COMBINED_SELLERS = 'combined_sellers'
TAB_STO = 'sto'
TAB_DETAILING = 'detailing'
TAB_TEST = 'test'

CONTACT_TABS = (
    (TAB_ALL, 'Все контакты'),
    (TAB_PARTS_BUYERS, 'По заявкам'),
    (TAB_MARKETPLACE_BUYERS, 'По покупкам товаров'),
    (TAB_SERVICE_CUSTOMERS, 'Заказчики услуг'),
    (TAB_PARTS_SELLERS, 'Получают заявки'),
    (TAB_MARKETPLACE_SELLERS, 'Размещают товары'),
    (TAB_COMBINED_SELLERS, 'Совмещают оба направления'),
    (TAB_STO, 'СТО'),
    (TAB_DETAILING, 'Детейлинг'),
    (TAB_TEST, 'Тестовые контакты'),
)

TAB_ROLE_MAP = {
    TAB_PARTS_BUYERS: {ROLE_PARTS_BUYER},
    TAB_MARKETPLACE_BUYERS: {ROLE_MARKETPLACE_BUYER},
    TAB_SERVICE_CUSTOMERS: {ROLE_SERVICE_CUSTOMER},
    TAB_PARTS_SELLERS: {ROLE_PARTS_SELLER},
    TAB_MARKETPLACE_SELLERS: {ROLE_MARKETPLACE_SELLER},
    TAB_STO: {ROLE_STO},
    TAB_DETAILING: {ROLE_DETAILING},
}

CATEGORY_PERIOD_CHOICES = (
    ('30', '30 дней'),
    ('90', '90 дней'),
    ('180', '180 дней'),
    ('all', 'За всё время'),
)

CATEGORY_SOURCE_CHOICES = (
    ('request', 'Заявка'),
    ('purchase', 'Покупка'),
    ('both', 'Заявка или покупка'),
)


@dataclass
class MarketingContact:
    phone_key: str
    masked_phone: str
    name: str
    city: str
    country: str
    roles: frozenset[str]
    last_activity: datetime | None
    requests_count: int | None
    orders_count: int | None
    products_count: int | None
    marketing_consent: str | None
    marketing_consent_label: str
    contact_status: str | None
    contact_status_label: str
    is_test: bool
    is_active: bool
    transport_types: frozenset[str]
    brands: frozenset[str]
    models: frozenset[str]
    categories: frozenset[str]
    display_roles: tuple[str, ...]
    category_interests: tuple[BuyerCategoryInterest, ...] = field(
        default_factory=tuple,
        repr=False,
    )


@dataclass
class ContactFilters:
    q: str = ''
    tab: str = TAB_ALL
    contact_type: str = ''
    role: str = ''
    country: str = ''
    city: str = ''
    activity_status: str = ''
    marketing_consent: str = ''
    last_activity_from: str = ''
    last_activity_to: str = ''
    is_test: str = ''
    transport_type: str = ''
    brand: str = ''
    model: str = ''
    category: str = ''
    category_source: str = ''
    category_period: str = ''


class _ContactBuilder:
    def __init__(self, phone_key: str) -> None:
        self.phone_key = phone_key
        self.masked_phone = mask_phone(phone_key)
        self.name = ''
        self.city = ''
        self.country = ''
        self.roles: set[str] = set()
        self.last_activity: datetime | None = None
        self.requests_count: int | None = None
        self.orders_count: int | None = None
        self.products_count: int | None = None
        self.marketing_consent: str | None = None
        self.contact_status: str | None = None
        self.is_test = False
        self.is_active = False
        self.transport_types: set[str] = set()
        self.brands: set[str] = set()
        self.models: set[str] = set()
        self.categories: set[str] = set()
        self.category_interests: list[BuyerCategoryInterest] = []
        self._has_parts_seller = False
        self._has_marketplace_seller = False

    def _touch_activity(self, dt: datetime | None) -> None:
        if dt is None:
            return
        if self.last_activity is None or dt > self.last_activity:
            self.last_activity = dt

    def merge_buyer_contact(self, buyer: BuyerContact) -> None:
        self.roles.add(ROLE_PARTS_BUYER)
        self.country = buyer.primary_country or self.country
        self.city = buyer.primary_city or self.city
        self.requests_count = buyer.requests_count
        self.contact_status = buyer.status
        self.is_test = buyer.is_test_contact
        self.is_active = buyer.status == BUYER_CONTACT_STATUS_ACTIVE
        self._touch_activity(buyer.last_request_at)

        marketing_consents = [
            consent
            for consent in buyer.consents.all()
            if consent.channel == CONTACT_CONSENT_CHANNEL_WHATSAPP
            and consent.purpose == CONTACT_CONSENT_PURPOSE_MARKETING
        ]
        if marketing_consents:
            self.marketing_consent = marketing_consents[0].status

        for vehicle in buyer.vehicles.all():
            if vehicle.transport_type:
                self.transport_types.add(vehicle.transport_type)
            if vehicle.brand:
                self.brands.add(vehicle.brand)
            if vehicle.model:
                self.models.add(vehicle.model)

        for interest in buyer.category_interests.all():
            if interest.category:
                self.categories.add(interest.category)
            self.category_interests.append(interest)

    def add_marketplace_buyer(
        self,
        *,
        orders_count: int,
        last_activity: datetime | None,
        name: str,
    ) -> None:
        self.roles.add(ROLE_MARKETPLACE_BUYER)
        self.orders_count = orders_count
        if name and not self.name:
            self.name = name
        self._touch_activity(last_activity)

    def add_service_customer(
        self,
        *,
        requests_count: int,
        last_activity: datetime | None,
        city: str,
        brand: str,
        model: str,
    ) -> None:
        self.roles.add(ROLE_SERVICE_CUSTOMER)
        if self.requests_count is None:
            self.requests_count = requests_count
        else:
            self.requests_count += requests_count
        if city and not self.city:
            self.city = city
        if brand:
            self.brands.add(brand)
        if model:
            self.models.add(model)
        self._touch_activity(last_activity)

    def add_parts_seller(self, seller: Seller) -> None:
        self.roles.add(ROLE_PARTS_SELLER)
        self._has_parts_seller = True
        if seller.name and not self.name:
            self.name = seller.name
        if seller.city and not self.city:
            self.city = seller.city
        seller_active = seller.is_active and not seller.is_paused
        self.is_active = self.is_active or seller_active

    def add_marketplace_seller(
        self,
        seller_profile: SellerProfile,
        *,
        products_count: int,
    ) -> None:
        self.roles.add(ROLE_MARKETPLACE_SELLER)
        self._has_marketplace_seller = True
        self.products_count = products_count
        if seller_profile.name and not self.name:
            self.name = seller_profile.name
        if seller_profile.city and not self.city:
            self.city = seller_profile.city
        self.is_active = True

    def add_service_seller(self, seller: ServiceSeller) -> None:
        role = ROLE_STO if seller.seller_type == 'sto' else ROLE_DETAILING
        self.roles.add(role)
        if seller.name and not self.name:
            self.name = seller.name
        if seller.city and not self.city:
            self.city = seller.city
        seller_active = seller.is_active and not seller.is_paused
        self.is_active = self.is_active or seller_active

    def finalize(self) -> MarketingContact:
        status_labels = dict(BuyerContact._meta.get_field('status').choices)
        display_roles = role_labels_from_builder(self)
        return MarketingContact(
            phone_key=self.phone_key,
            masked_phone=self.masked_phone,
            name=self.name,
            city=self.city,
            country=self.country,
            roles=frozenset(self.roles),
            last_activity=self.last_activity,
            requests_count=self.requests_count,
            orders_count=self.orders_count,
            products_count=self.products_count,
            marketing_consent=self.marketing_consent,
            marketing_consent_label=marketing_consent_label(self.marketing_consent),
            contact_status=self.contact_status,
            contact_status_label=status_labels.get(self.contact_status, '—')
            if self.contact_status
            else '—',
            is_test=self.is_test,
            is_active=self.is_active,
            transport_types=frozenset(self.transport_types),
            brands=frozenset(self.brands),
            models=frozenset(self.models),
            categories=frozenset(self.categories),
            display_roles=display_roles,
            category_interests=tuple(self.category_interests),
        )


def role_labels_from_builder(builder: _ContactBuilder) -> tuple[str, ...]:
    labels = [ROLE_LABELS[role] for role in ROLE_LABELS if role in builder.roles]
    if builder._has_parts_seller and builder._has_marketplace_seller:
        labels.append('Совмещает оба направления')
    return tuple(labels)


def build_contact_registry() -> dict[str, MarketingContact]:
    registry: dict[str, _ContactBuilder] = {}

    def get_builder(raw_phone: object) -> _ContactBuilder | None:
        phone_key = normalize_phone_key(raw_phone)
        if not phone_key:
            return None
        if phone_key not in registry:
            registry[phone_key] = _ContactBuilder(phone_key)
        return registry[phone_key]

    marketing_consent_qs = ContactConsent.objects.filter(
        channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
        purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
    )
    buyer_qs = BuyerContact.objects.prefetch_related(
        Prefetch('consents', queryset=marketing_consent_qs),
        'vehicles',
        'category_interests',
    )
    for buyer in buyer_qs:
        builder = get_builder(buyer.phone_normalized)
        if builder:
            builder.merge_buyer_contact(buyer)

    test_phone_keys = get_test_marketplace_phone_keys()
    order_stats = iter_marketplace_order_phone_stats()
    for row in order_stats:
        builder = get_builder(row['customer_phone'])
        if builder:
            builder.add_marketplace_buyer(
                orders_count=row['orders_count'],
                last_activity=row['last_activity'],
                name=row['customer_name'] or '',
            )
            phone_key = normalize_phone_key(row['customer_phone'])
            if phone_key and phone_key in test_phone_keys:
                builder.is_test = True

    service_stats = (
        ServiceRequest.objects.exclude(phone='')
        .values('phone')
        .annotate(
            requests_count=Count('id'),
            last_activity=Max('created_at'),
            city=Max('city'),
            brand=Max('brand'),
            model=Max('model'),
        )
    )
    for row in service_stats:
        builder = get_builder(row['phone'])
        if builder:
            builder.add_service_customer(
                requests_count=row['requests_count'],
                last_activity=row['last_activity'],
                city=row['city'] or '',
                brand=row['brand'] or '',
                model=row['model'] or '',
            )

    for seller in Seller.objects.only(
        'whatsapp',
        'name',
        'city',
        'is_active',
        'is_paused',
    ):
        builder = get_builder(seller.whatsapp)
        if builder:
            builder.add_parts_seller(seller)

    product_counts: dict[str, int] = {}
    for row in Product.objects.exclude(whatsapp_number='').values('whatsapp_number').annotate(
        products_count=Count('id'),
    ):
        phone_key = normalize_phone_key(row['whatsapp_number'])
        if phone_key:
            product_counts[phone_key] = product_counts.get(phone_key, 0) + row['products_count']

    for profile in SellerProfile.objects.only('phone', 'name', 'city'):
        builder = get_builder(profile.phone)
        if builder:
            phone_key = normalize_phone_key(profile.phone)
            builder.add_marketplace_seller(
                profile,
                products_count=product_counts.get(phone_key or '', 0),
            )

    for seller in ServiceSeller.objects.only(
        'whatsapp',
        'name',
        'city',
        'seller_type',
        'is_active',
        'is_paused',
    ):
        builder = get_builder(seller.whatsapp)
        if builder:
            builder.add_service_seller(seller)

    return {key: builder.finalize() for key, builder in registry.items()}


def list_contacts(filters: ContactFilters) -> list[MarketingContact]:
    contacts = list(build_contact_registry().values())
    return [contact for contact in contacts if _matches_filters(contact, filters)]


def _has_combined_seller_roles(contact: MarketingContact) -> bool:
    return ROLE_PARTS_SELLER in contact.roles and ROLE_MARKETPLACE_SELLER in contact.roles


def _matches_tab(contact: MarketingContact, tab: str) -> bool:
    if tab == TAB_ALL:
        return True
    if tab == TAB_TEST:
        return contact.is_test
    if tab == TAB_COMBINED_SELLERS:
        return _has_combined_seller_roles(contact)
    required_roles = TAB_ROLE_MAP.get(tab)
    if not required_roles:
        return True
    if tab != TAB_TEST and contact.is_test:
        return False
    return bool(contact.roles & required_roles)


def _parse_date(value: str):
    if not value:
        return None
    try:
        from datetime import date

        parts = value.split('-')
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (IndexError, TypeError, ValueError):
        return None


def _category_period_start(period: str) -> datetime | None:
    if period == 'all' or not period:
        return None
    try:
        days = int(period)
    except (TypeError, ValueError):
        return None
    return timezone.now() - timedelta(days=days)


def _matches_category_filters(contact: MarketingContact, filters: ContactFilters) -> bool:
    if not filters.category and not filters.category_source and not filters.category_period:
        return True
    if filters.category_source == 'purchase':
        return False
    if not contact.category_interests:
        return not filters.category
    category_norm = normalize_buyer_text(filters.category) if filters.category else ''
    period_start = _category_period_start(filters.category_period)
    for interest in contact.category_interests:
        if category_norm and interest.category_normalized != category_norm:
            continue
        if period_start and interest.last_seen_at and interest.last_seen_at < period_start:
            continue
        if period_start and not interest.last_seen_at:
            continue
        return True
    return not filters.category


def _matches_filters(contact: MarketingContact, filters: ContactFilters) -> bool:
    if not _matches_tab(contact, filters.tab):
        return False

    query = (filters.q or '').strip().casefold()
    if query:
        haystacks = [
            contact.masked_phone.casefold(),
            contact.phone_key,
            contact.name.casefold(),
            contact.city.casefold(),
        ]
        digit_query = ''.join(ch for ch in query if ch.isdigit())
        matched = any(query in value for value in haystacks if value)
        if digit_query and digit_query in contact.phone_key:
            matched = True
        if not matched:
            return False

    if filters.contact_type == 'test' and not contact.is_test:
        return False
    if filters.contact_type == 'real' and contact.is_test:
        return False

    if filters.role and filters.role not in contact.roles:
        return False

    if filters.country:
        country_norm = normalize_buyer_text(filters.country)
        if normalize_buyer_text(contact.country) != country_norm:
            return False

    if filters.city:
        city_norm = normalize_buyer_text(filters.city)
        if normalize_buyer_text(contact.city) != city_norm:
            return False

    if filters.activity_status == 'active' and not contact.is_active:
        return False
    if filters.activity_status == 'inactive' and contact.is_active:
        return False

    if filters.marketing_consent:
        consent = contact.marketing_consent or 'missing'
        if filters.marketing_consent == 'missing' and contact.marketing_consent is not None:
            return False
        if filters.marketing_consent != 'missing' and consent != filters.marketing_consent:
            return False

    activity_from = _parse_date(filters.last_activity_from)
    activity_to = _parse_date(filters.last_activity_to)
    if activity_from or activity_to:
        if contact.last_activity is None:
            return False
        activity_date = contact.last_activity.date()
        if activity_from and activity_date < activity_from:
            return False
        if activity_to and activity_date > activity_to:
            return False

    if filters.is_test == 'yes' and not contact.is_test:
        return False
    if filters.is_test == 'no' and contact.is_test:
        return False

    if filters.transport_type and filters.transport_type not in contact.transport_types:
        return False

    if filters.brand:
        brand_norm = normalize_buyer_text(filters.brand)
        if not any(normalize_buyer_text(brand) == brand_norm for brand in contact.brands):
            return False

    if filters.model:
        model_norm = normalize_buyer_text(filters.model)
        if not any(normalize_buyer_text(model) == model_norm for model in contact.models):
            return False

    if not _matches_category_filters(contact, filters):
        return False

    return True


def sort_contacts(contacts: list[MarketingContact]) -> list[MarketingContact]:
    return sorted(
        contacts,
        key=lambda contact: (
            contact.last_activity is None,
            -(contact.last_activity.timestamp() if contact.last_activity else 0),
            contact.phone_key,
        ),
    )


def role_labels(contact: MarketingContact) -> list[str]:
    labels = [ROLE_LABELS[role] for role in ROLE_LABELS if role in contact.roles]
    if _has_combined_seller_roles(contact):
        labels.append('Совмещает оба направления')
    return labels


def filter_options(registry: dict[str, MarketingContact]) -> dict[str, list[str]]:
    cities: set[str] = set()
    countries: set[str] = set()
    brands: set[str] = set()
    models: set[str] = set()
    categories: set[str] = set()
    for contact in registry.values():
        if contact.city:
            cities.add(contact.city)
        if contact.country:
            countries.add(contact.country)
        brands.update(contact.brands)
        models.update(contact.models)
        categories.update(contact.categories)
    return {
        'cities': sorted(cities, key=str.casefold),
        'countries': sorted(countries, key=str.casefold),
        'brands': sorted(brands, key=str.casefold),
        'models': sorted(models, key=str.casefold),
        'categories': sorted(categories, key=str.casefold),
    }
