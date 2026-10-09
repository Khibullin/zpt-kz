from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from marketing.services.campaigns.live_processor import process_marketing_live_send_batch
from marketing.services.campaigns.send_settings import marketing_live_whatsapp_send_enabled
from core.models import SellerLeadPipelineRun


class Command(BaseCommand):
    help = 'Process a batch of queued LIVE marketing WhatsApp messages.'

    def _run_manual_first_circle_growth_once(self) -> bool:
        marker = 'FIRST_CIRCLE_MANUAL_20261009_1014'
        existing = SellerLeadPipelineRun.objects.filter(search_term=marker).first()
        if existing is not None:
            return False

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
        try:
            call_command(
                'run_first_circle_seller_growth',
                stdout=self.stdout,
                stderr=self.stderr,
            )
        except Exception as exc:
            run.status = SellerLeadPipelineRun.STATUS_FAILED
            run.error_message = f'{type(exc).__name__}: {exc}'[:2000]
            run.finished_at = timezone.now()
            run.save(update_fields=['status', 'error_message', 'finished_at'])
            raise

        run.status = SellerLeadPipelineRun.STATUS_SUCCESS
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'finished_at'])
        return True

    def handle(self, *args, **options):
        if self._run_manual_first_circle_growth_once():
            self.stdout.write('Manual first-circle seller growth completed.')
            return
        if not marketing_live_whatsapp_send_enabled():
            self.stdout.write('MARKETING_WHATSAPP_SEND_MODE is not LIVE — nothing to process.')
            return

        result = process_marketing_live_send_batch()
        self.stdout.write(
            f'Processed={result.processed_count} sent={result.sent_count} '
            f'failed={result.failed_count} skipped={result.skipped_count} '
            f'remaining={result.remaining_queued}',
        )
