from django.core.management.base import BaseCommand

from marketing.services.campaigns.live_processor import process_marketing_live_send_batch
from marketing.services.campaigns.send_settings import marketing_live_whatsapp_send_enabled


class Command(BaseCommand):
    help = 'Process a batch of queued LIVE marketing WhatsApp messages.'

    def handle(self, *args, **options):
        # Temporary bounded one-off runner for the 2026-10-08 seller directory
        # audit.  It is intentionally isolated from marketing sends and is
        # removed after the campaign reaches complete=True.
        try:
            from core.services.seller_directory_audit_campaign import (
                process_seller_directory_audit_batch,
            )

            audit = process_seller_directory_audit_batch()
            self.stdout.write(
                'SELLER_DIRECTORY_AUDIT '
                f'registered={audit.registered_audited} '
                f'registered_changed={audit.registered_changed} '
                f'classified={audit.lead_classified} '
                f'classification_errors={audit.lead_classification_errors} '
                f'classification_remaining={audit.classification_remaining} '
                f'websites_scanned={audit.websites_scanned} '
                f'website_errors={audit.website_scan_errors} '
                f'website_remaining={audit.website_scan_remaining} '
                f'new_whatsapp={audit.curated_whatsapp_added} '
                f'curated_changes={audit.curated_lead_changes} '
                f'complete={int(audit.complete)}'
            )
        except Exception as exc:
            self.stderr.write(
                f'SELLER_DIRECTORY_AUDIT error={type(exc).__name__}: {exc}'[:1000]
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
