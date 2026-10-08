from django.core.management.base import BaseCommand

from marketing.services.campaigns.live_processor import process_marketing_live_send_batch
from core.services.seller_whatsapp_recheck_campaign import process_seller_whatsapp_recheck_batch
from marketing.services.campaigns.send_settings import marketing_live_whatsapp_send_enabled


class Command(BaseCommand):
    help = 'Process a batch of queued LIVE marketing WhatsApp messages.'

    def handle(self, *args, **options):
        try:
            recheck_results = process_seller_whatsapp_recheck_batch(batch_size=2)
            for result in recheck_results:
                self.stdout.write(
                    'SELLER_WHATSAPP_RECHECK '
                    f'lead={result.lead_id or "-"} outcome={result.outcome} '
                    f'found={int(result.found_whatsapp)} remaining={result.remaining} '
                    f'sources={",".join(result.active_sources) or "-"}'
                    + (f' error={result.error}' if result.error else '')
                )
        except Exception as exc:
            self.stderr.write(
                f'SELLER_WHATSAPP_RECHECK internal_error={type(exc).__name__}: {exc}'[:500]
            )

        if not marketing_live_whatsapp_send_enabled():
            self.stdout.write('MARKETING_WHATSAPP_SEND_MODE is not LIVE — nothing to process.')
            return

        result = process_marketing_live_send_batch()
        self.stdout.write(
            f'Processed={result.processed_count} sent={result.sent_count} '
            f'failed={result.failed_count} skipped={result.skipped_count} '
            f'remaining={result.remaining_queued}',
        )
