from __future__ import annotations

import hmac
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.models import SellerLead
from core.services.seller_contact_enrichment import (
    SellerContactEnrichmentError,
    enrich_seller_lead_contacts,
)

RECHECK_CUTOFF = datetime(2026, 10, 8, 10, 34, 22, tzinfo=dt_timezone.utc)
TERMINAL_LIFECYCLES = {
    SellerLead.LIFECYCLE_DUPLICATE,
    SellerLead.LIFECYCLE_REJECTED,
    SellerLead.LIFECYCLE_CLOSED,
    SellerLead.LIFECYCLE_CLAIMED,
    SellerLead.LIFECYCLE_VERIFIED,
    SellerLead.LIFECYCLE_ACTIVE,
}
TARGET_SOURCE_TYPES = (
    'instagram',
    'web_search',
    'brave_search',
    'google_places',
    'website',
    'two_gis',
)


def _authorized(request) -> bool:
    expected = str(getattr(settings, 'SELLER_RECHECK_TOKEN', '') or '')
    supplied = str(request.headers.get('X-ZPT-Ops-Token') or '')
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def _targets():
    return (
        SellerLead.objects
        .filter(duplicate_of__isnull=True, whatsapp='')
        .exclude(lifecycle_status__in=TERMINAL_LIFECYCLES)
        .filter(
            Q(instagram_username__gt='')
            | Q(source_type='web_search')
            | Q(sources__source_type__in=TARGET_SOURCE_TYPES)
        )
        .filter(
            Q(last_enrichment_attempt_at__isnull=True)
            | Q(last_enrichment_attempt_at__lte=RECHECK_CUTOFF)
        )
        .distinct()
        .order_by('id')
    )


@csrf_exempt
@require_POST
def seller_whatsapp_recheck_batch(request):
    if not _authorized(request):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)

    try:
        batch_size = int(request.GET.get('limit') or '1')
    except ValueError:
        batch_size = 1
    batch_size = max(1, min(batch_size, 2))

    before = _targets().count()
    processed = []
    found = 0
    failures = 0

    for lead in list(_targets()[:batch_size]):
        prior = lead.whatsapp
        try:
            result = enrich_seller_lead_contacts(
                lead,
                sources=['all'],
                dry_run=False,
                stop_on_verified_whatsapp=True,
            )
            lead.refresh_from_db()
            has_whatsapp = bool(lead.whatsapp)
            if has_whatsapp and not prior:
                found += 1
            processed.append({
                'id': lead.pk,
                'outcome': result.outcome,
                'whatsapp': bool(lead.whatsapp),
                'verified': list(result.verified_whatsapp),
                'errors': len(result.errors),
            })
        except Exception as exc:
            failures += 1
            SellerLead.objects.filter(pk=lead.pk).update(
                last_enrichment_attempt_at=timezone.now(),
                last_enrichment_result=f'oneoff_error:{type(exc).__name__}'[:40],
                updated_at=timezone.now(),
            )
            processed.append({
                'id': lead.pk,
                'outcome': 'error',
                'whatsapp': False,
                'error_type': type(exc).__name__,
            })

    remaining = _targets().count()
    total_with_whatsapp = SellerLead.objects.filter(
        duplicate_of__isnull=True,
    ).exclude(whatsapp='').count()

    return JsonResponse({
        'ok': True,
        'cutoff': RECHECK_CUTOFF.isoformat(),
        'before': before,
        'processed': processed,
        'found_in_batch': found,
        'failures': failures,
        'remaining': remaining,
        'total_with_whatsapp': total_with_whatsapp,
        'done': remaining == 0,
    })
