"""Random aliases for existing expiring, scoped seller request links."""
import secrets
from django.conf import settings
from django.db import transaction
from django.urls import reverse
from core.models import SellerRequestAccess


@transaction.atomic
def short_url(access, *, telegram=False):
    current = SellerRequestAccess.objects.select_for_update().get(pk=access.pk)
    if not current.short_code:
        while True:
            code = secrets.token_urlsafe(12)  # 96 random bits, never request/seller IDs.
            if not SellerRequestAccess.objects.filter(short_code=code).exists():
                break
        current.short_code = code
        current.save(update_fields=['short_code'])
    access.short_code = current.short_code
    base = str(getattr(settings, 'PUBLIC_BASE_URL', 'https://zpt.kz') or 'https://zpt.kz').rstrip('/')
    name = 'manual_short_telegram' if telegram else 'manual_short_request'
    return base + reverse(name, args=[current.short_code])
