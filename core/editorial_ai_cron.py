"""Authenticated, bounded single-item AI draft job. No automatic publishing."""
import hmac

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone

from core.editorial_ai import improve_draft_with_ai
from core.models import EditorialAIExecution, EditorialPage


@csrf_exempt
@require_POST
def editorial_ai_trigger(request):
    expected = (getattr(settings, 'EDITORIAL_CRON_TOKEN', '') or '').strip()
    if not expected or not hmac.compare_digest(
        request.headers.get('Authorization', ''), f'Bearer {expected}'
    ):
        return JsonResponse({'error': 'not found'}, status=404)
    if not getattr(settings, 'EDITORIAL_AI_ENABLED', False) or not getattr(settings, 'OPENAI_API_KEY', ''):
        return JsonResponse({'error': 'AI disabled'}, status=503)

    today = timezone.localdate()
    with transaction.atomic():
        # A hard, persistent cap of two attempts per local day.
        if EditorialAIExecution.objects.filter(day=today).count() >= 2:
            return JsonResponse({'status': 'daily_limit'})
        page = (
            EditorialPage.objects.filter(
                status=EditorialPage.STATUS_DRAFT,
                source_candidate__isnull=False,
            )
            .exclude(pk__in=EditorialAIExecution.objects.values('page_id'))
            .order_by('created_at', 'pk')
            .first()
        )
        if page is None:
            return JsonResponse({'status': 'no_pending_drafts'})
        try:
            attempt = EditorialAIExecution.objects.create(
                page=page, day=today, status='running'
            )
        except IntegrityError:
            return JsonResponse({'status': 'already_claimed'})

    try:
        improved = improve_draft_with_ai(page.pk)
        attempt.status = 'complete'
        attempt.save(update_fields=['status'])
        return JsonResponse({'status': 'improved', 'draft_id': improved.pk})
    except Exception as exc:
        attempt.status = 'failed'
        attempt.save(update_fields=['status'])
        return JsonResponse(
            {'status': 'failed', 'draft_id': page.pk, 'error': type(exc).__name__},
            status=502,
        )
