from django.core.management.base import BaseCommand

from marketing.services.campaigns.live_processor import process_marketing_live_send_batch
from marketing.services.campaigns.send_settings import marketing_live_whatsapp_send_enabled


class Command(BaseCommand):
    help = 'Process a batch of queued LIVE marketing WhatsApp messages.'

    def handle(self, *args, **options):
        if not marketing_live_whatsapp_send_enabled():
            self.stdout.write('MARKETING_WHATSAPP_SEND_MODE is not LIVE — nothing to process.')
            return

        result = process_marketing_live_send_batch()
        self.stdout.write(
            f'Processed={result.processed_count} sent={result.sent_count} '
            f'failed={result.failed_count} skipped={result.skipped_count} '
            f'remaining={result.remaining_queued}',
        )
