"""Process due SellerLead rows through the existing contact enrichment service.

Does not send WhatsApp, does not create Seller rows, and does not enable
2GIS or Yandex. Disabled sources stay disabled.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandError

from core.services.seller_contact_enrichment import (
    SellerContactEnrichmentError,
    enrich_seller_lead_contacts,
)
from core.services.seller_lead_classification import classify_seller_lead
from core.services.seller_lead_classification_selection import select_leads_needing_classification
from core.services.seller_lead_enrichment_schedule import (
    apply_enrichment_schedule,
    claim_due_seller_leads,
    classify_enrichment_result,
    due_seller_leads,
)
from core.services.seller_lead_market import qualify_market

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 25
MAX_BATCH_SIZE = 100


class Command(BaseCommand):
    help = (
        'Квалифицирует SellerLead локально и обогащает только подошедшие due-записи. '
        'Не отправляет сообщения и не создаёт продавцов.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=DEFAULT_BATCH_SIZE)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        dry_run = bool(options['dry_run'])
        apply = bool(options['apply'])
        if dry_run == apply:
            raise CommandError('Укажите ровно один режим: --dry-run или --apply.')
        batch_size = options['batch_size']
        if batch_size < 1 or batch_size > MAX_BATCH_SIZE:
            raise CommandError(f'--batch-size должен быть от 1 до {MAX_BATCH_SIZE}.')

        self._qualify(batch_size, dry_run=dry_run)
        if dry_run:
            leads = list(due_seller_leads()[:batch_size])
            self.stdout.write(f'due={len(leads)} dry-run')
            for lead in leads:
                self.stdout.write(f'#{lead.pk} {lead.name} | dry-run | next={lead.next_enrichment_at}')
            return

        leads = claim_due_seller_leads(limit=batch_size)
        self.stdout.write(f'claimed={len(leads)}')
        for lead in leads:
            try:
                result = enrich_seller_lead_contacts(lead, sources=('all',), dry_run=False)
            except SellerContactEnrichmentError as exc:
                if _is_configuration_error(exc):
                    raise CommandError(str(exc)) from exc
                self._record_failure(lead, exc)
                continue
            except Exception as exc:
                self._record_failure(lead, exc)
                continue
            code = classify_enrichment_result(result)
            apply_enrichment_schedule(lead, code)
            lead.save(update_fields=[
                'last_enrichment_result',
                'enrichment_attempt_count',
                'next_enrichment_at',
                'updated_at',
            ])
            self.stdout.write(f'#{lead.pk} {code} next={lead.next_enrichment_at}')

    def _qualify(self, batch_size: int, *, dry_run: bool) -> None:
        leads = list(select_leads_needing_classification(limit=batch_size))
        for lead in leads:
            if dry_run:
                planned = classify_seller_lead(lead, dry_run=True)
                market, reason = qualify_market(lead)
                self.stdout.write(
                    f'#{lead.pk} qualify dry-run business_type={planned} market={market} {reason}'
                )
                continue
            classify_seller_lead(lead, promote_lifecycle=True)
            lead.refresh_from_db()
            self.stdout.write(
                f'#{lead.pk} qualified business_type={lead.business_type} '
                f'market={lead.market_scope}'
            )

    def _record_failure(self, lead, exc: Exception) -> None:
        logger.exception('Seller lead enrichment failed lead=%s', lead.pk)
        message = f'{type(exc).__name__}: {exc}'[:500]
        self.stdout.write(f'#{lead.pk} failed error {message}')
        apply_enrichment_schedule(lead, 'error')
        lead.save(update_fields=[
            'last_enrichment_result',
            'enrichment_attempt_count',
            'next_enrichment_at',
            'updated_at',
        ])


def _is_configuration_error(exc: SellerContactEnrichmentError) -> bool:
    text = str(exc).casefold()
    return 'не задан' in text or 'enabled' in text or '=false' in text
