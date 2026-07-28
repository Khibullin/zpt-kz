from __future__ import annotations

RECIPIENT_TYPE_PARTS_REQUEST_BUYERS = 'parts_request_buyers'
RECIPIENT_TYPE_MARKETPLACE_BUYERS = 'marketplace_buyers'
RECIPIENT_TYPE_SELLERS = 'sellers'

RECIPIENT_TYPE_CHOICES = (
    (RECIPIENT_TYPE_PARTS_REQUEST_BUYERS, 'Покупатели по заявкам на запчасти'),
    (RECIPIENT_TYPE_MARKETPLACE_BUYERS, 'Покупатели Marketplace'),
    (RECIPIENT_TYPE_SELLERS, 'Продавцы'),
)

RECIPIENT_TYPE_VALUES = {choice[0] for choice in RECIPIENT_TYPE_CHOICES}

# Marketplace brand filter: Order → OrderItem → Product.brand exists structurally,
# but marketing registry does not aggregate purchase brands onto contacts yet.
MARKETPLACE_BRAND_FILTER_AVAILABLE = False

SESSION_DRAFT_KEY = 'marketing_simple_mailing_draft'
PREVIEW_SESSION_KEY = 'marketing_simple_mailing_preview'

RECIPIENT_SCOPE_CONTROL_ONLY = 'control_only'
RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS = 'audience_plus_controls'

RECIPIENT_SCOPE_CHOICES = (
    (RECIPIENT_SCOPE_CONTROL_ONLY, 'Контрольная рассылка — только контрольные номера'),
    (RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS, 'Рабочая рассылка — выбранные получатели + контрольные номера'),
)

RECIPIENT_SCOPE_VALUES = {choice[0] for choice in RECIPIENT_SCOPE_CHOICES}
DEFAULT_RECIPIENT_SCOPE = RECIPIENT_SCOPE_CONTROL_ONLY

# Explicit test-only brand labels stored in production data (e.g. QA requests).
TEST_BRAND_RAW_NAMES = (
    'TestBrand',
)
