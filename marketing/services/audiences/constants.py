from __future__ import annotations

GROUP_BUYERS = 'buyers'
GROUP_SELLERS = 'sellers'
GROUP_SERVICE_PROVIDERS = 'service_providers'
GROUP_TEST = 'test_contacts'

CONTACT_GROUPS = (
    (GROUP_BUYERS, 'Покупатели'),
    (GROUP_SELLERS, 'Продавцы'),
    (GROUP_SERVICE_PROVIDERS, 'Исполнители услуг'),
    (GROUP_TEST, 'Тестовые контакты'),
)

SUBTYPE_PARTS_REQUESTS = 'parts_requests'
SUBTYPE_MARKETPLACE_PAID = 'marketplace_paid'
SUBTYPE_SERVICE_REQUESTS = 'service_requests'
SUBTYPE_ALL_BUYERS = 'all_buyers'

SUBTYPE_REQUEST_SELLERS = 'request_sellers'
SUBTYPE_MARKETPLACE_SELLERS = 'marketplace_sellers'
SUBTYPE_COMBINED_SELLERS = 'combined_sellers'
SUBTYPE_ALL_SELLERS = 'all_sellers'

SUBTYPE_STO = 'sto'
SUBTYPE_DETAILING = 'detailing'
SUBTYPE_ALL_SERVICE_PROVIDERS = 'all_service_providers'

SUBTYPE_TEST_CONTACTS = 'test_contacts'

BUYER_SUBTYPES = (
    (SUBTYPE_PARTS_REQUESTS, 'По заявкам на запчасти'),
    (SUBTYPE_MARKETPLACE_PAID, 'По оплаченным покупкам товаров'),
    (SUBTYPE_SERVICE_REQUESTS, 'Заказчики услуг'),
    (SUBTYPE_ALL_BUYERS, 'Все покупатели'),
)

SELLER_SUBTYPES = (
    (SUBTYPE_REQUEST_SELLERS, 'Получают заявки'),
    (SUBTYPE_MARKETPLACE_SELLERS, 'Размещают товары'),
    (SUBTYPE_COMBINED_SELLERS, 'Совмещают оба направления'),
    (SUBTYPE_ALL_SELLERS, 'Все продавцы'),
)

SERVICE_SUBTYPES = (
    (SUBTYPE_STO, 'СТО'),
    (SUBTYPE_DETAILING, 'Детейлинг'),
    (SUBTYPE_ALL_SERVICE_PROVIDERS, 'Все исполнители'),
)

TEST_SUBTYPES = (
    (SUBTYPE_TEST_CONTACTS, 'Тестовые контакты'),
)

GROUP_SUBTYPE_MAP: dict[str, tuple[tuple[str, str], ...]] = {
    GROUP_BUYERS: BUYER_SUBTYPES,
    GROUP_SELLERS: SELLER_SUBTYPES,
    GROUP_SERVICE_PROVIDERS: SERVICE_SUBTYPES,
    GROUP_TEST: TEST_SUBTYPES,
}

PREVIEW_LIMIT = 50
AUDIENCE_LIST_PAGE_SIZE = 25

SEARCH_SCOPE_CHOICES = (
    ('city', 'Город'),
    ('kazakhstan', 'Казахстан'),
    ('custom', 'Выбранные города'),
)

TRANSPORT_TYPE_CHOICES = (
    ('car', 'Легковой'),
    ('truck', 'Грузовой'),
)

CATEGORY_PERIOD_CHOICES = (
    ('30', '30 дней'),
    ('90', '90 дней'),
    ('180', '180 дней'),
    ('all', 'За всё время'),
)

ACTIVITY_PERIOD_CHOICES = (
    ('last_30_days', '30 дней'),
    ('last_90_days', '90 дней'),
    ('last_180_days', '180 дней'),
    ('all', 'За всё время'),
)

EXCLUSION_LABELS = {
    'eligible': 'Допустим к отправке',
    'test_contact': 'Тестовый контакт',
    'inactive': 'Неактивный',
    'invalid_phone': 'Некорректный телефон',
    'consent_granted': 'Согласие дано (не в eligible)',
    'consent_unknown': 'Согласие не подтверждено',
    'consent_revoked': 'Согласие отозвано',
    'consent_not_recorded': 'Рекламное согласие не зафиксировано',
    'marketplace_test': 'Тестовый оплаченный покупатель',
}
