"""Local definition of the next buyer receipt template.

The approved production template ``zpt_buyer_request_receipt`` lives in Meta.
Its footer «ZPT.KZ — заявки на автозапчасти» is a static FOOTER component of
that template. The send payload only passes the template name, five body
parameters and two URL-button suffixes, so application code cannot remove
the footer from the current template.

``zpt_buyer_request_receipt_v2`` is prepared here and is not submitted.
``WHATSAPP_BUYER_TEMPLATE_NAME`` stays on the approved name until Meta
returns status APPROVED for v2. The body text is not copied into the repo:
submitting a guessed body would create a different template. Copy the
approved body from Meta before any submit, keep the footer empty, and do
not call the template management POST until that review.
"""
from __future__ import annotations

PRODUCTION_TEMPLATE_NAME = 'zpt_buyer_request_receipt'
PREPARED_TEMPLATE_NAME = 'zpt_buyer_request_receipt_v2'
LANGUAGE_CODE = 'ru'

# Static footer on the approved Meta template. It is not part of the send payload.
PRODUCTION_FOOTER_TEXT = 'ZPT.KZ — заявки на автозапчасти'

# v2 must be created without a footer. Empty string omits the FOOTER component
# in build_meta_template_payload.
FOOTER_TEXT = ''
HEADER_TEXT = ''

# Intentionally blank. Fill from the approved Meta template before submit.
BODY_TEXT = ''
READY_TO_SUBMIT = False
ACTIVATED = False

BODY_VARIABLES = (
    'request_id',
    'vehicle',
    'category',
    'city',
    'sellers_count',
)

BUTTONS = (
    {
        'type': 'url',
        'text': 'Открыть заявку',
        'url': 'https://zpt.kz/my-request/{{1}}',
    },
    {
        'type': 'url',
        'text': 'Мои заявки',
        'url': 'https://zpt.kz/my-requests/{{1}}',
    },
)


def prepared_template_meta_components() -> list[dict]:
    """Components that would be sent to Meta once BODY_TEXT is copied.

    FOOTER is omitted while FOOTER_TEXT is empty. An empty body is not a
    submit-ready payload.
    """
    components: list[dict] = []
    if HEADER_TEXT.strip():
        components.append({
            'type': 'HEADER',
            'format': 'TEXT',
            'text': HEADER_TEXT.strip(),
        })
    if BODY_TEXT:
        components.append({
            'type': 'BODY',
            'text': BODY_TEXT,
        })
    if FOOTER_TEXT.strip():
        components.append({
            'type': 'FOOTER',
            'text': FOOTER_TEXT.strip(),
        })
    if BUTTONS:
        components.append({
            'type': 'BUTTONS',
            'buttons': [
                {
                    'type': 'URL',
                    'text': button['text'],
                    'url': button['url'],
                }
                for button in BUTTONS
            ],
        })
    return components
