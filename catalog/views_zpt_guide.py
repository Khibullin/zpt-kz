"""Public ZPT Гид page: helper, FAQ, feedback, install."""

from __future__ import annotations

from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from core.forms import FeedbackForm
from core.platform_help import load_conversation_from_session
from core.services.seller_identity import get_logged_request_seller
from core.zpt_guide_faq import FAQ_SECTIONS


@ensure_csrf_cookie
@require_GET
def zpt_guide_view(request):
    seller = get_logged_request_seller(request)
    conversation = load_conversation_from_session(request)
    help_contact_is_seller = seller is not None
    help_contact_whatsapp = ''
    if seller is not None and str(getattr(seller, 'whatsapp', '') or '').strip():
        help_contact_whatsapp = str(seller.whatsapp).strip()
    elif conversation is not None and conversation.contact_whatsapp:
        help_contact_whatsapp = conversation.contact_whatsapp
    return render(request, 'catalog/zpt_guide.html', {
        'faq_sections': FAQ_SECTIONS,
        'feedback_form': FeedbackForm(),
        'help_contact_whatsapp': help_contact_whatsapp,
        'help_contact_is_seller': help_contact_is_seller,
    })
