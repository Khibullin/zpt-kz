import os
import sys

from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'core'

    def ready(self):
        token = (os.getenv('SELLER_MANUAL_BUILD_GROWTH_TOKEN') or '').strip()
        if not token:
            return
        if len(sys.argv) < 2 or sys.argv[1] != 'collectstatic':
            return

        from django.core.management import call_command
        from django.utils import timezone
        from core.models import SellerLeadPipelineRun

        marker = f'BUILD_FIRST_CIRCLE_{token}'[:200]
        if SellerLeadPipelineRun.objects.filter(search_term=marker).exists():
            print(f'{marker}: already completed or started; skipping.')
            return

        run = SellerLeadPipelineRun.objects.create(
            trigger=SellerLeadPipelineRun.TRIGGER_MANUAL,
            status=SellerLeadPipelineRun.STATUS_RUNNING,
            is_dry_run=False,
            city='FIRST_CIRCLE_19',
            category='seller_growth',
            search_limit=20,
            lead_limit=3,
            max_queries_per_lead=1,
            search_term=marker,
            rotation_enabled=False,
            skip_discovery=False,
            skip_enrichment=False,
            cooldown_minutes=0,
            force_run=True,
        )
        print(f'{marker}: starting approved one-off seller growth.')
        try:
            call_command('run_first_circle_seller_growth')
        except Exception as exc:
            run.status = SellerLeadPipelineRun.STATUS_FAILED
            run.error_message = f'{type(exc).__name__}: {exc}'[:2000]
            run.finished_at = timezone.now()
            run.save(update_fields=['status', 'error_message', 'finished_at'])
            print(f'{marker}: failed: {type(exc).__name__}: {exc}')
            raise

        run.status = SellerLeadPipelineRun.STATUS_SUCCESS
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'finished_at'])
        print(f'{marker}: completed successfully.')
