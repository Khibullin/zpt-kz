from marketing.services.simple_mailing.brands import (
    SimpleMailingValidationError,
    get_available_brands,
    has_brand_selection,
    marketplace_brand_filter_enabled,
    normalize_brand_selection,
    validate_brand_selection,
)
from marketing.services.simple_mailing.constants import (
    MARKETPLACE_BRAND_FILTER_AVAILABLE,
    RECIPIENT_TYPE_CHOICES,
    RECIPIENT_TYPE_MARKETPLACE_BUYERS,
    RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
    RECIPIENT_TYPE_SELLERS,
    RECIPIENT_TYPE_VALUES,
    PREVIEW_SESSION_KEY,
    SESSION_DRAFT_KEY,
)
from marketing.services.simple_mailing.draft import (
    clear_simple_mailing_draft,
    load_draft_template_id,
    load_simple_mailing_draft,
    save_simple_mailing_draft,
    save_template_to_draft,
)
from marketing.services.simple_mailing.purpose import recipient_type_to_campaign_purpose
from marketing.services.simple_mailing.template_preview import (
    get_language_label,
    render_simple_mailing_template_preview,
)
from marketing.services.simple_mailing.templates import (
    build_template_cards,
    resolve_selected_template,
    template_still_available,
)
from marketing.services.simple_mailing.preview import (
    build_selection_key,
    clear_preview_state,
    load_preview_state,
    preview_matches,
    save_preview_state,
)
from marketing.services.simple_mailing.recipients import (
    SimpleMailingPreviewRow,
    SimpleMailingRecipientsResult,
    SimpleMailingSelection,
    resolve_simple_mailing_recipients,
)

__all__ = [
    'MARKETPLACE_BRAND_FILTER_AVAILABLE',
    'PREVIEW_SESSION_KEY',
    'RECIPIENT_TYPE_CHOICES',
    'RECIPIENT_TYPE_MARKETPLACE_BUYERS',
    'RECIPIENT_TYPE_PARTS_REQUEST_BUYERS',
    'RECIPIENT_TYPE_SELLERS',
    'RECIPIENT_TYPE_VALUES',
    'SESSION_DRAFT_KEY',
    'SimpleMailingValidationError',
    'SimpleMailingPreviewRow',
    'SimpleMailingRecipientsResult',
    'SimpleMailingSelection',
    'build_selection_key',
    'build_template_cards',
    'clear_preview_state',
    'clear_simple_mailing_draft',
    'get_available_brands',
    'get_language_label',
    'has_brand_selection',
    'load_draft_template_id',
    'load_preview_state',
    'load_simple_mailing_draft',
    'marketplace_brand_filter_enabled',
    'normalize_brand_selection',
    'preview_matches',
    'recipient_type_to_campaign_purpose',
    'render_simple_mailing_template_preview',
    'resolve_selected_template',
    'resolve_simple_mailing_recipients',
    'save_preview_state',
    'save_simple_mailing_draft',
    'save_template_to_draft',
    'template_still_available',
    'validate_brand_selection',
]
