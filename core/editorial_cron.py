"""Authenticated daily bridge for a Render cron without database credentials."""
import hmac
import io
from datetime import date

from django.conf import settings
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone

from core.models import EditorialDailyExecution


@csrf_exempt
@require_POST
def editorial_daily_trigger(request):
    expected = (getattr(settings, 'EDITORIAL_CRON_TOKEN', '') or '').strip()
    given = request.headers.get('Authorization', '')
    if not expected or not hmac.compare_digest(given, f'Bearer {expected}'):
        return JsonResponse({'error': 'not found'}, status=404)
    if not getattr(settings, 'EDITORIAL_CRON_ENABLED', False):
        return JsonResponse({'error': 'disabled'}, status=503)

    local_day = timezone.localdate()
    try:
        with transaction.atomic():
            attempt = EditorialDailyExecution.objects.create(day=local_day, status='running')
    except IntegrityError:
        return JsonResponse({'status': 'already_executed', 'date': str(local_day)})

    out = io.StringIO()
    try:
        # This fast bridge generates private template drafts only.
        # AI rewriting must remain a separate, bounded background worker.
        call_command('run_editorial_daily', limit=2, ai=False, stdout=out)
        call_command('prepare_editorial_social_drafts', stdout=out)
        attempt.status = 'complete'
        attempt.save(update_fields=['status'])
        return JsonResponse({'status':'complete', 'date':str(local_day)})
    except Exception:
        attempt.status = 'failed'
        attempt.save(update_fields=['status'])
        return JsonResponse({'error':'daily generation failed'}, status=500)
