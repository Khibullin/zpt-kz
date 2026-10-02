"""Enrich SellerLead contacts from a public website.

Not wired to cron. Does not send WhatsApp, email, or SMS, and does not create
Seller, User, SellerProfile, or Product rows. Kolesa is not called.
"""

from django.core.management.base import BaseCommand, CommandError

from core.models import SellerLead
from core.services.seller_contact_enrichment import (
    ALLOWED_SOURCES,
    SellerContactEnrichmentError,
    enrich_seller_lead_contacts,
)

MAX_LIMIT = 5


class Command(BaseCommand):
    help = (
        'Ищет публичные контакты SellerLead на официальном сайте. '
        'Не отправляет сообщения и не создаёт продавцов.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--lead-id', action='append', dest='lead_ids', type=int, default=[])
        parser.add_argument('--city', default='')
        parser.add_argument('--limit', type=int, default=1)
        parser.add_argument('--source', action='append', dest='sources', default=[])
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        dry_run = bool(options['dry_run'])
        apply = bool(options['apply'])
        if dry_run and apply:
            raise CommandError('Укажите только один режим: --dry-run или --apply.')
        if not dry_run and not apply:
            raise CommandError('Укажите --dry-run или --apply. Без режима сеть и запись не выполняются.')

        sources = [str(item).strip().lower().replace('-', '_') for item in options['sources']]
        if not sources:
            raise CommandError('Укажите --source website, --source google_places или --source brave.')
        unknown = [item for item in sources if item not in ALLOWED_SOURCES and item != 'kolesa']
        if unknown:
            raise CommandError(f'Неизвестный источник enrichment: {unknown[0]}')

        limit = options['limit']
        if limit < 1 or limit > MAX_LIMIT:
            raise CommandError(f'--limit должен быть от 1 до {MAX_LIMIT}.')

        leads = list(self._leads(options['lead_ids'], options['city'], limit))
        if not leads:
            raise CommandError('SellerLead для обогащения не найден.')

        for lead in leads:
            try:
                result = enrich_seller_lead_contacts(
                    lead,
                    sources=sources,
                    dry_run=dry_run,
                )
            except SellerContactEnrichmentError as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(
                f'#{lead.pk} {lead.name} | {result.outcome} | '
                f'contacts={len(result.observations)} | dry_run={result.dry_run}'
            )
            for observation in result.observations:
                self.stdout.write(
                    f'  [{observation.field_name}] {observation.value} '
                    f'confidence={observation.confidence} explicit={observation.explicit_whatsapp}'
                )
            for message in result.errors:
                self.stdout.write(self.style.WARNING(message))
        if dry_run:
            self.stdout.write(self.style.WARNING('Dry-run: записи в базу не сохранялись.'))
        else:
            self.stdout.write(self.style.SUCCESS(
                'Контакты обновлены. Продавцы, пользователи и товары не создавались. '
                'Сообщения не отправлялись.',
            ))

    def _leads(self, lead_ids: list[int], city: str, limit: int):
        if lead_ids:
            return SellerLead.objects.filter(pk__in=lead_ids).order_by('pk')
        if city:
            return SellerLead.objects.filter(city=city).order_by('pk')[:limit]
        raise CommandError('Укажите --lead-id или --city.')
