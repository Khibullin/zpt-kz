from __future__ import annotations

from marketing.services.campaigns.send_settings import get_marketing_whatsapp_send_mode


def marketing_send_mode(request):
    mode = get_marketing_whatsapp_send_mode()
    return {
        'marketing_whatsapp_send_mode': mode,
        'marketing_whatsapp_send_mode_label': mode,
    }
