from __future__ import annotations

from django.core.management.base import BaseCommand

from marketing.services.campaigns.live_processor import (
    list_stuck_live_processing_messages,
    mark_stuck_live_processing_as_delivery_unknown,
)
from marketing.services.campaigns.send_settings import marketing_live_whatsapp_send_enabled


class Command(BaseCommand):
    help = (
        'Audit LIVE messages stuck in processing. '
        'Use --mark-delivery-unknown to classify them without Meta resend.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--mark-delivery-unknown',
            action='store_true',
            help='Mark stuck processing messages as delivery_unknown (no Meta send).',
        )

    def handle(self, *args, **options):
        if not marketing_live_whatsapp_send_enabled():
            self.stdout.write(
                self.style.WARNING(
                    'MARKETING_WHATSAPP_SEND_MODE is not LIVE; audit only lists data.',
                ),
            )

        stuck_messages = list_stuck_live_processing_messages()
        if not stuck_messages:
            self.stdout.write('No LIVE messages in processing status.')
            return

        for message in stuck_messages:
            self.stdout.write(
                f'message #{message.pk} run #{message.send_run_id} '
                f'campaign #{message.send_run.campaign_id} '
                f'attempted_at={message.attempted_at or "—"}',
            )

        if options['mark_delivery_unknown']:
            updated = mark_stuck_live_processing_as_delivery_unknown(
                message_ids=[message.pk for message in stuck_messages],
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f'Marked {updated} processing message(s) as delivery_unknown.',
                ),
            )
