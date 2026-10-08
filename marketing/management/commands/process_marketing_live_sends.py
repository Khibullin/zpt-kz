from django.core.management.base import BaseCommand

from marketing.services.campaigns.live_processor import process_marketing_live_send_batch
from marketing.services.campaigns.send_settings import marketing_live_whatsapp_send_enabled


class Command(BaseCommand):
    help = 'Process a batch of queued LIVE marketing WhatsApp messages.'

    def handle(self, *args, **options):
        # Temporary bounded runner for the registered-seller public audit.
        # Progress is persisted in a dedicated temporary table. Once all rows
        # are done this is a cheap no-op and the hook/table are removed.
        try:
            from core.services.registered_seller_public_audit import (
                process_registered_seller_public_audit_batch,
            )

            audit = process_registered_seller_public_audit_batch(batch_size=4)
            self.stdout.write(
                'REGISTERED_SELLER_PUBLIC_AUDIT '
                f'claimed={audit.claimed} '
                f'completed={audit.completed} '
                f'errors={audit.errors} '
                f'websites_added={audit.websites_added} '
                f'instagrams_added={audit.instagrams_added} '
                f'curated_changes={audit.curated_changes} '
                f'remaining={audit.remaining} '
                f'complete={int(audit.complete)}'
            )
        except Exception as exc:
            self.stderr.write(
                f'REGISTERED_SELLER_PUBLIC_AUDIT error={type(exc).__name__}: {exc}'[:1000]
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
